# -*- coding: utf-8 -*-
"""Abstract base class for file parsers.

A :class:`ParserBase` subclass handles **one file format**.  Its job
is to read a file's raw bytes and produce a list of
:class:`~agentscope.rag.Section` objects, each representing a natural
boundary of the source (e.g. one PDF page, one PPTX slide, one
embedded image).

Parsers **do not chunk text**.  Long text is left intact inside the
Section; splitting happens later in a
:class:`~agentscope.rag.ChunkerBase`.  Parsers also do not need to
worry about output size — only about preserving the structural
boundaries that downstream consumers must not cross.
"""
from abc import ABC, abstractmethod

from .._document import Section


class ParserBase(ABC):
    """Abstract base class for file-format parsers.

    Each subclass handles a single file format (or a related family,
    e.g. all plain-text MIME types).  Subclasses are typically
    instantiated once and reused across many ``parse()`` calls.

    Subclasses should be stateless or thread-safe — a single
    instance may be invoked concurrently from multiple agent runs.

    Subclasses must declare :attr:`supported_media_types` so that the
    KnowledgeBaseManager can route uploaded files to the right parser
    based on standard IANA media types (RFC 6838).
    """

    supported_media_types: list[str]
    """Standard IANA media types (RFC 6838) this parser handles,
    e.g. ``["application/pdf"]`` or
    ``["text/plain", "text/markdown"]``.  Used by the
    KnowledgeBaseManager to select a parser for an uploaded file."""

    @abstractmethod
    async def parse(
        self,
        file: bytes,
        filename: str,
    ) -> list[Section]:
        """Parse a file into a list of :class:`Section` objects.

        Args:
            file (`bytes`):
                The raw file content.
            filename (`str`):
                The original filename (e.g. ``"report.pdf"``).  Used
                for error messages and copied into each Section's
                :attr:`Section.source` field for downstream display
                / citation.

        Returns:
            `list[Section]`:
                One Section per natural boundary in the source file.
                For unstructured formats (plain text, image, video),
                a single Section may cover the whole file.  Sections
                are returned in document order.

        Raises:
            `ValueError`: If the file cannot be parsed.
        """
