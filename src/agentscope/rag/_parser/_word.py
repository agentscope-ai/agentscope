# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Word (.docx) file parser.

Walks the document element-by-element and emits one :class:`Section`
per contiguous content block — adjacent paragraphs (and, by default,
tables) are merged into a single text section; embedded images are
emitted as their own :class:`DataBlock` sections.

Supported options: ``include_image``, ``separate_table``,
``table_format``.  Chunking is **not** done here — long text stays
intact inside a section and is split downstream by a
:class:`~agentscope.rag.ChunkerBase`.
"""
from __future__ import annotations

import base64
import io
from typing import Literal, TYPE_CHECKING

from ..._logging import logger
from ...message import Base64Source, DataBlock, TextBlock
from .._document import Section
from ._base import ParserBase
from ._utils import (
    _guess_image_media_type,
    _table_to_json,
    _table_to_markdown,
)

if TYPE_CHECKING:
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

_VML_NS = "{urn:schemas-microsoft-com:vml}"


def _extract_text_from_paragraph(para: DocxParagraph) -> str:
    """Extract text from a paragraph, including text in text boxes and
    VML shapes.

    Tries three methods in order:
    1. All ``w:t`` elements in the paragraph XML (covers revisions,
       hyperlinks, etc.).
    2. The standard ``para.text`` property.
    3. Text inside ``w:txbxContent`` and VML ``v:textbox`` elements.
    """
    from docx.oxml.ns import qn

    text = ""
    for t_elem in para._element.findall(".//" + qn("w:t")):
        if t_elem.text:
            text += t_elem.text

    if not text:
        text = para.text.strip()

    if not text:
        for txbx in para._element.findall(".//" + qn("w:txbxContent")):
            for p_elem in txbx.findall(".//" + qn("w:p")):
                for t_elem in p_elem.findall(".//" + qn("w:t")):
                    if t_elem.text:
                        text += t_elem.text

        for vml_tb in para._element.findall(".//" + _VML_NS + "textbox"):
            for p_elem in vml_tb.findall(".//" + qn("w:p")):
                for t_elem in p_elem.findall(".//" + qn("w:t")):
                    if t_elem.text:
                        text += t_elem.text

    return text.strip()


def _extract_table_data(table: DocxTable) -> list[list[str]]:
    """Extract table data from a python-docx Table, preserving line breaks
    within cells.

    Horizontal merges (``w:gridSpan``) are expanded with empty strings so
    that every row has the same number of columns.  Vertically merged
    continuation cells (``w:vMerge`` without ``val="restart"``) are kept
    as-is — their XML content is typically empty, which is the desired
    behaviour for downstream renderers.
    """
    from docx.oxml.ns import qn

    table_data: list[list[str]] = []
    for tr in table._element.findall(qn("w:tr")):
        row_data: list[str] = []
        for tc in tr.findall(qn("w:tc")):
            paragraphs: list[str] = []
            for p_elem in tc.findall(qn("w:p")):
                texts: list[str] = []
                for t_elem in p_elem.findall(".//" + qn("w:t")):
                    if t_elem.text:
                        texts.append(t_elem.text)
                para_text = "".join(texts)
                if para_text:
                    paragraphs.append(para_text)
            row_data.append("\n".join(paragraphs))

            tc_pr = tc.find(qn("w:tcPr"))
            if tc_pr is not None:
                gs = tc_pr.find(qn("w:gridSpan"))
                if gs is not None:
                    span = int(
                        gs.get(qn("w:val"), gs.get("val", "1")),
                    )
                    row_data.extend([""] * (span - 1))

        table_data.append(row_data)
    return table_data


def _extract_image_blocks(
    para: DocxParagraph,
    filename: str,
) -> list[DataBlock]:
    """Extract image blocks from a paragraph (both modern ``w:drawing``
    and legacy ``w:pict`` / VML formats)."""
    from docx.oxml.ns import qn

    blocks: list[DataBlock] = []

    for drawing in para._element.findall(".//" + qn("w:drawing")):
        for blip in drawing.findall(".//" + qn("a:blip")):
            embed = blip.get(qn("r:embed"))
            if not embed:
                continue
            try:
                part = para.part.related_parts[embed]
                media_type = part.content_type or _guess_image_media_type(
                    part.blob,
                )
                blocks.append(
                    DataBlock(
                        source=Base64Source(
                            media_type=media_type,
                            data=base64.b64encode(part.blob).decode("utf-8"),
                        ),
                        name=filename,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to extract image: %s", e)

    for pict in para._element.findall(".//" + qn("w:pict")):
        for imagedata in pict.findall(".//" + _VML_NS + "imagedata"):
            rel_id = imagedata.get(qn("r:id"))
            if not rel_id:
                continue
            try:
                part = para.part.related_parts[rel_id]
                media_type = part.content_type or _guess_image_media_type(
                    part.blob,
                )
                blocks.append(
                    DataBlock(
                        source=Base64Source(
                            media_type=media_type,
                            data=base64.b64encode(part.blob).decode("utf-8"),
                        ),
                        name=filename,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to extract VML image: %s", e)

    return blocks


class WordParser(ParserBase):
    """Parser for Word ``.docx`` files.

    Document order is preserved.  Elements are visited sequentially and
    grouped into a minimum number of sections:

    - **Paragraphs** contribute to one running text section.
    - **Tables** are rendered as Markdown or JSON and, depending on
      ``separate_table``, either merged into the running text or emitted
      as standalone text sections.
    - **Images** emit standalone :class:`Section` instances holding a
      :class:`DataBlock` with the base64-encoded image bytes.
    """

    supported_media_types: list[str] = [
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
        ".document",
    ]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Return ``[".docx"]`` — the only format ``python-docx`` reads."""
        return [".docx"]

    def __init__(
        self,
        include_image: bool = True,
        separate_table: bool = False,
        table_format: Literal["markdown", "json"] = "markdown",
    ) -> None:
        """Initialize the Word parser.

        Args:
            include_image (`bool`, defaults to ``True``):
                When ``True``, embedded images are emitted as
                :class:`DataBlock` sections.  Set to ``False`` to keep
                a text-only index.
            separate_table (`bool`, defaults to ``False``):
                When ``True``, each table becomes its own text section,
                never merged with surrounding paragraphs.
            table_format (`Literal["markdown", "json"]`, defaults to
                ``"markdown"``):
                How to render tables.  ``"markdown"`` uses pipe-table
                syntax; ``"json"`` emits a JSON array prefixed with a
                ``<system-info>`` marker.

        Raises:
            `ValueError`: If ``table_format`` is not one of
                ``"markdown"`` / ``"json"``.
        """
        if table_format not in ("markdown", "json"):
            raise ValueError(
                "The table_format must be one of 'markdown' or 'json', "
                f"got {table_format!r}.",
            )
        self.include_image = include_image
        self.separate_table = separate_table
        self.table_format = table_format

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Parse a DOCX file into a list of :class:`Section` objects.

        Args:
            file (`bytes | str`):
                Either the raw DOCX bytes, or a filesystem path to the
                DOCX file.
            filename (`str`):
                The source filename, copied into each Section's
                :attr:`Section.source`.

        Returns:
            `list[Section]`:
                Sections in document order.

        Raises:
            `FileNotFoundError`: If ``file`` is a ``str`` pointing to
                a path that does not exist.
            `ImportError`: If :mod:`python-docx` is not installed.
            `ValueError`: If the bytes cannot be parsed.
        """
        try:
            from docx import Document as DocxDocument
            from docx.oxml import CT_P, CT_Tbl
            from docx.text.paragraph import Paragraph
            from docx.table import Table
            from docx.oxml.ns import qn
        except ImportError as e:
            raise ImportError(
                "Please install python-docx to use the Word parser. "
                "You can install it by `pip install python-docx` (or "
                "`pip install agentscope[rag]`).",
            ) from e

        if isinstance(file, str):
            doc = DocxDocument(file)
        else:
            doc = DocxDocument(io.BytesIO(file))

        sections: list[Section] = []
        text_buffer: list[str] = []

        def flush_text() -> None:
            if not text_buffer:
                return
            sections.append(
                Section(
                    content=TextBlock(text="\n".join(text_buffer)),
                    source=filename,
                    metadata={},
                ),
            )
            text_buffer.clear()

        for element in doc.element.body:
            if isinstance(element, CT_P):
                para = Paragraph(element, doc)

                text = _extract_text_from_paragraph(para)
                if text:
                    text_buffer.append(text)

                if self.include_image:
                    has_drawing = bool(
                        para._element.findall(".//" + qn("w:drawing")),
                    )
                    has_pict = bool(
                        para._element.findall(".//" + qn("w:pict")),
                    )
                    if has_drawing or has_pict:
                        flush_text()
                        for block in _extract_image_blocks(para, filename):
                            sections.append(
                                Section(
                                    content=block,
                                    source=filename,
                                    metadata={
                                        "media_type": (
                                            block.source.media_type
                                        ),
                                    },
                                ),
                            )

            elif isinstance(element, CT_Tbl):
                table_data = _extract_table_data(Table(element, doc))
                rendered = (
                    _table_to_markdown(table_data)
                    if self.table_format == "markdown"
                    else _table_to_json(table_data)
                )
                if not rendered:
                    continue

                if self.separate_table:
                    flush_text()
                    sections.append(
                        Section(
                            content=TextBlock(text=rendered),
                            source=filename,
                            metadata={},
                        ),
                    )
                else:
                    text_buffer.append(rendered)

        flush_text()
        return sections
