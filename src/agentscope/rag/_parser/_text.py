# -*- coding: utf-8 -*-
"""Plain-text file parser."""
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

    def __init__(self, encoding: str = "utf-8") -> None:
        """Initialize the text parser.

        Args:
            encoding (`str`, defaults to ``"utf-8"``):
                The text encoding used to decode the file bytes.
        """
        self.encoding = encoding

    async def parse(
        self,
        file: bytes,
        filename: str,
    ) -> list[Section]:
        """Read the file as text and return a single :class:`Section`.

        Args:
            file (`bytes`):
                The raw file content.
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
        try:
            text = file.decode(self.encoding)
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Failed to decode {filename!r} as " f"{self.encoding!r}: {e}",
            ) from e

        return [
            Section(
                content=TextBlock(text=text),
                source=filename,
                metadata={},
            ),
        ]
