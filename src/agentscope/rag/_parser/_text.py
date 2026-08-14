# -*- coding: utf-8 -*-
"""Plain-text file parser."""
import os

from ...message import TextBlock
from .._document import Section
from ._base import ParserBase


class TextParser(ParserBase):
    """Parser for plain-text file formats.

    Reads the entire file as UTF-8 text and returns a single
    :class:`Section`.  No internal boundaries are inferred — the file
    is treated as one unstructured blob, leaving all splitting to a
    downstream :class:`~agentscope.rag.ChunkerBase`.

    Supports a fixed set of standard text-based IANA media types
    (``text/plain``, ``text/markdown``, ``text/csv``, …).  Use
    ``TextParser.supported_media_types`` to enumerate them.
    """

    supported_media_types: list[str] = [
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "text/x-rst",
        "application/json",
        "application/xml",
        "application/x-yaml",
    ]
    """Standard IANA media types this parser handles."""

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Return the human-friendly text extensions.

        Override the base reverse-lookup because
        :func:`mimetypes.guess_all_extensions` returns a long tail of
        developer-tool extensions for ``text/plain`` (``.bat`` /
        ``.c`` / ``.pl`` / ``.ksh`` / …) that have no place in a KB
        file picker, and returns the empty list for
        ``application/x-yaml``.
        """
        return [
            ".csv",
            ".htm",
            ".html",
            ".json",
            ".markdown",
            ".md",
            ".rst",
            ".txt",
            ".xml",
            ".yaml",
            ".yml",
        ]

    def __init__(self, encoding: str = "utf-8") -> None:
        """Initialize the text parser.

        Args:
            encoding (`str`, defaults to ``"utf-8"``):
                The text encoding used to decode the file bytes.
        """
        self.encoding = encoding

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Read the file as text and return a single :class:`Section`.

        Args:
            file (`bytes | str`):
                The file content.  ``bytes`` is decoded with the
                configured encoding.  ``str`` is disambiguated at
                runtime: if it names an existing file on disk the
                file is read and decoded; otherwise it is used
                verbatim as pre-decoded text — letting local-mode
                callers skip the encode → decode round trip.
            filename (`str`):
                The source filename, copied verbatim into
                :attr:`Section.source`.

        Returns:
            `list[Section]`:
                Always a one-element list containing the entire file
                contents.

        Raises:
            `ValueError`: If the bytes cannot be decoded with the
                configured encoding.
        """
        if isinstance(file, str):
            if os.path.isfile(file):
                with open(file, "rb") as fp:
                    raw = fp.read()
                try:
                    text = raw.decode(self.encoding)
                except UnicodeDecodeError as e:
                    raise ValueError(
                        f"Failed to decode {filename!r} as "
                        f"{self.encoding!r}: {e}",
                    ) from e
            else:
                text = file
        else:
            try:
                text = file.decode(self.encoding)
            except UnicodeDecodeError as e:
                raise ValueError(
                    f"Failed to decode {filename!r} as "
                    f"{self.encoding!r}: {e}",
                ) from e

        return [
            Section(
                content=TextBlock(text=text),
                source=filename,
                metadata={},
            ),
        ]
