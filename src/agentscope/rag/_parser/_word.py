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
from collections.abc import Iterator
from typing import Any, Literal, TYPE_CHECKING

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
_MATH_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_MC_ALTERNATE_CONTENT = (
    "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
    "AlternateContent"
)


def _extract_text_from_paragraph(para: DocxParagraph) -> str:
    """Extract text from a paragraph, including any text box anchored to it.

    The paragraph's own runs come first, then each text box once, as its own
    line. Reading every ``w:t`` under the paragraph in one pass would splice a
    text box into the middle of the surrounding sentence, and would read the
    ``mc:Fallback`` copy of a text box as a second occurrence of the same text.
    Each ``mc:AlternateContent`` containing a text box uses the first branch
    with readable Word text, retaining the fallback when its choices cannot
    supply any text. Paragraph boundaries within each text box are retained.
    Other paragraph content keeps its original behavior.

    Args:
        para (`DocxParagraph`):
            The python-docx paragraph to read.

    Returns:
        `str`:
            The paragraph's text, with each text box on its own line.
    """
    from docx.oxml.ns import qn

    text_tag = qn("w:t")
    paragraph_tag = qn("w:p")
    text_box_tag = qn("w:txbxContent")
    root = para._element

    def iter_text(element: Any) -> Iterator[Any]:
        """Visit text in one readable branch of each AlternateContent."""
        if element.tag == text_tag:
            yield element
        elif (
            element.tag == _MC_ALTERNATE_CONTENT
            and element.find(".//" + text_box_tag) is not None
        ):
            texts: list[Any] = []
            for branch in element:
                texts = list(iter_text(branch))
                if any(t.text and t.text.strip() for t in texts):
                    break
            yield from texts
        else:
            for child in element:
                yield from iter_text(child)

    own_parts: list[str] = []
    text_boxes: dict[Any, dict[Any, list[str]]] = {}
    for text in iter_text(root):
        if not text.text:
            continue
        parts = own_parts
        paragraph = root
        for ancestor in text.iterancestors():
            if ancestor is root:
                break
            if ancestor.tag == paragraph_tag:
                paragraph = ancestor
            if ancestor.tag == text_box_tag:
                parts = text_boxes.setdefault(ancestor, {}).setdefault(
                    paragraph,
                    [],
                )
                break
        parts.append(text.text)

    own_text = "".join(own_parts)
    if not own_text and not text_boxes:
        # python-docx also reads characters such as w:noBreakHyphen.
        own_text = para.text
    blocks = [
        own_text,
        *(
            "\n".join("".join(parts) for parts in paragraphs.values())
            for paragraphs in text_boxes.values()
        ),
    ]
    return "\n".join(block.strip() for block in blocks if block.strip())


def _is_blank_paragraph(para: DocxParagraph) -> bool:
    """Return whether Word renders *para* as a blank line.

    A blank line has no text and no picture. It may still hold a run:
    typing a space before Enter leaves ``<w:t> </w:t>``, and Shift+Enter
    on an empty line leaves a run holding only ``<w:br/>``. A run that
    carries anything else (field instructions, a symbol, an equation) is
    not blank even when the extractor finds no text in it.
    """
    from docx.oxml.ns import qn

    root = para._element
    for tag in ("w:drawing", "w:pict", "w:object"):
        if root.findall(".//" + qn(tag)):
            return False
    if root.findall(".//" + _MATH_NS + "oMath"):
        return False

    blank_run_tags = {
        qn(tag)
        for tag in (
            "w:rPr",
            "w:br",
            "w:cr",
            "w:tab",
            "w:lastRenderedPageBreak",
            "w:softHyphen",
        )
    }
    text_tag = qn("w:t")
    for run in root.findall(".//" + qn("w:r")):
        for child in run:
            if child.tag == text_tag:
                if (child.text or "").strip():
                    return False
            elif child.tag not in blank_run_tags:
                return False
    return True


def _extract_table_data(table: DocxTable) -> list[list[str]]:
    """Extract table data from a python-docx Table, preserving line breaks
    within cells.

    Horizontal merges (``w:gridSpan``) are expanded with empty strings so
    that every row has the same number of columns. Omitted leading and
    trailing cells (``w:gridBefore`` / ``w:gridAfter``) are also padded to
    preserve their grid positions. Vertically merged
    continuation cells (``w:vMerge`` without ``val="restart"``) are kept
    as-is — their XML content is typically empty, which is the desired
    behaviour for downstream renderers.
    """
    from docx.oxml.ns import qn

    text_tag = qn("w:t")
    break_tags = {qn("w:br"), qn("w:cr")}
    table_data: list[list[str]] = []
    for tr in table._element.findall(qn("w:tr")):
        row_data: list[str] = []
        grid_before = tr.find(f"{qn('w:trPr')}/{qn('w:gridBefore')}")
        if grid_before is not None:
            row_data.extend([""] * int(grid_before.get(qn("w:val"), "0")))
        for tc in tr.findall(qn("w:tc")):
            paragraphs: list[str] = []
            # Include paragraphs of tables nested in this cell.
            for p_elem in tc.xpath("./w:p | ./w:tbl//w:tc/w:p"):
                text_parts: list[str] = []
                for element in p_elem.iter():
                    if element.tag == text_tag and element.text:
                        text_parts.append(element.text)
                    elif element.tag in break_tags:
                        text_parts.append("\n")
                para_text = "".join(text_parts)
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

        grid_after = tr.find(f"{qn('w:trPr')}/{qn('w:gridAfter')}")
        if grid_after is not None:
            row_data.extend([""] * int(grid_after.get(qn("w:val"), "0")))
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


def _body_elements(body: Any) -> Iterator[Any]:
    """Yield the top-level content elements of a document body.

    Block-level content controls (``w:sdt`` — how Word writes its
    automatic table of contents and every developer-mode block control)
    are containers, not content: their ``w:sdtContent`` children are
    yielded in their place, recursively, so a control cannot hide a
    paragraph or a table.  Inline ``w:sdt`` needs no handling here, as
    its runs are already descendants of the paragraph holding it.

    Args:
        body (`Any`):
            The ``w:body`` element of a python-docx document.

    Yields:
        `Any`:
            Each body child, with content controls replaced by theirs.
    """
    from docx.oxml.ns import qn

    for element in body:
        if element.tag == qn("w:sdt"):
            content = element.find(qn("w:sdtContent"))
            if content is not None:
                yield from _body_elements(content)
        else:
            yield element


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
                syntax, escaping pipes and rendering cell line breaks
                as ``<br>``; ``"json"`` emits a JSON array prefixed with
                a ``<system-info>`` marker and preserves extracted cell
                strings without Markdown rendering.

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
            with open(file, "rb") as fp:
                file = fp.read()

        try:
            doc = DocxDocument(io.BytesIO(file))
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(
                f"Failed to parse {filename!r} as DOCX: {e}",
            ) from e

        sections: list[Section] = []
        text_buffer: list[str] = []

        def flush_text() -> None:
            while text_buffer and text_buffer[-1] == "":
                text_buffer.pop()
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

        for element in _body_elements(doc.element.body):
            if isinstance(element, CT_P):
                para = Paragraph(element, doc)

                text = _extract_text_from_paragraph(para)
                if text:
                    text_buffer.append(text)
                elif text_buffer and _is_blank_paragraph(para):
                    # A blank line in Word: an empty <w:p>, a run holding
                    # only spaces, or a run holding only <w:br/>/<w:cr/>.
                    text_buffer.append("")

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

                if rendered:
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

                # Images pasted into table cells live under the table's
                # ``w:p`` elements, not under body-level paragraphs; without
                # this pass they are silently dropped.
                if self.include_image:
                    image_blocks: list[DataBlock] = []
                    for p_elem in element.findall(".//" + qn("w:p")):
                        # The outer paragraph scan already covers images in
                        # nested text-box paragraphs.
                        if p_elem.xpath("ancestor::w:p"):
                            continue
                        image_blocks.extend(
                            _extract_image_blocks(
                                Paragraph(p_elem, doc),
                                filename,
                            ),
                        )
                    if image_blocks:
                        flush_text()
                        for block in image_blocks:
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

        flush_text()
        return sections
