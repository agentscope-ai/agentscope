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
import mimetypes
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

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Filename extensions (including the leading ``.``) this parser
        can produce uploads for.

        The base implementation derives extensions from
        :attr:`supported_media_types` via
        :func:`mimetypes.guess_all_extensions` — good enough for clean
        IANA types like ``application/pdf``.  Subclasses **should
        override** this when the default reverse-lookup is noisy
        (``text/plain`` resolves to ``.bat`` / ``.c`` / ``.pl`` and a
        dozen other developer extensions no KB user wants in the file
        picker) or when a media type has no registered extension at all
        (``application/x-yaml`` returns the empty list).

        The result is consumed by the front-end's ``<input accept>`` and
        by the client-side filename guard; it is **not** consulted for
        media-type routing — that always goes through
        :attr:`supported_media_types`.

        Returns:
            `list[str]`:
                Deduplicated, sorted extensions (each starting with
                ``.``).  May be empty when no media type resolves.
        """
        out: set[str] = set()
        for media_type in cls.supported_media_types:
            out.update(mimetypes.guess_all_extensions(media_type))
        return sorted(out)

    @abstractmethod
    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Parse a file into a list of :class:`Section` objects.

        The ``file`` argument is a union covering the three call sites
        a parser sees in practice:

        - ``bytes`` — the raw payload, as handed in by HTTP uploads
          and blob-store reads.
        - ``str`` for binary parsers (PDF, PPT, image, …) — a
          **filesystem path** to the file to read.  The parser opens
          the path itself; callers do not need to read the bytes first.
        - ``str`` for :class:`TextParser` — disambiguated at runtime:
          if the string names an existing file on disk it is treated
          as a path and the file is decoded with the configured
          encoding; otherwise it is treated as pre-decoded text.

        Args:
            file (`bytes | str`):
                The file content or a path to it (see above).
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
            `TypeError`: If the subclass does not accept the supplied
                ``file`` form.
            `FileNotFoundError`: If a binary parser is handed a
                ``str`` that does not name an existing file.
            `ValueError`: If the file cannot be parsed.
        """
