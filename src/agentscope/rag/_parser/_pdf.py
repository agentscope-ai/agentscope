# -*- coding: utf-8 -*-
"""PDF file parser.

One :class:`Section` per page so a downstream
:class:`~agentscope.rag.ChunkerBase` never combines text across page
boundaries.  Each section's :attr:`Section.metadata` carries the
page number (starting at 1) for later citation.
"""
import io

from ...message import TextBlock
from .._document import Section
from ._base import ParserBase


class PDFParser(ParserBase):
    """Parser for PDF files.

    Returns one :class:`Section` per page.  Empty / image-only pages
    still produce a Section (with empty text) so the page → section
    correspondence stays exact — downstream chunkers naturally drop
    empty content.

    Requires :mod:`pypdf`; install with ``pip install pypdf`` (or via
    the ``agentscope[rag]`` extra).
    """

    supported_media_types: list[str] = ["application/pdf"]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Return the canonical ``.pdf`` extension."""
        return [".pdf"]

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Read the PDF bytes and return one Section per page.

        Args:
            file (`bytes | str`):
                Either the raw PDF bytes, or a filesystem path to
                the PDF file.
            filename (`str`):
                The source filename, copied verbatim into each
                Section's :attr:`Section.source` field.

        Returns:
            `list[Section]`:
                One Section per page, in document order.  Each
                section's metadata holds ``{"page": <starting at 1>}``.

        Raises:
            `FileNotFoundError`: If ``file`` is a ``str`` pointing to
                a path that does not exist.
            `ImportError`: If :mod:`pypdf` is not installed.
            `ValueError`: If the bytes cannot be parsed as PDF.
        """
        if isinstance(file, str):
            with open(file, "rb") as fp:
                file = fp.read()

        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
        except ImportError as e:
            raise ImportError(
                "Please install pypdf to use the PDF parser. "
                "You can install it by `pip install pypdf` (or "
                "`pip install agentscope[rag]`).",
            ) from e

        try:
            reader = PdfReader(io.BytesIO(file))
        except PdfReadError as e:
            raise ValueError(
                f"Failed to parse {filename!r} as PDF: {e}",
            ) from e

        sections: list[Section] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            sections.append(
                Section(
                    content=TextBlock(text=text),
                    source=filename,
                    metadata={"page": page_idx},
                ),
            )
        return sections
