# -*- coding: utf-8 -*-
"""Parser for HTML documents."""

import os
import re
from html.parser import HTMLParser as BaseHTMLParser

from ...message import TextBlock
from .._document import Section
from ._base import ParserBase


class _HTMLTextExtractor(BaseHTMLParser):
    """Internal HTML parser to extract text and ignore scripts/styles."""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._ignore_tags = {
            "script",
            "style",
            "head",
            "meta",
            "link",
            "noscript",
            "svg",
        }
        self._current_tags: list[str] = []
        self._void_elements = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "source", "track", "wbr",
        }

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag_lower = tag.lower()
        if tag_lower not in self._void_elements:
            self._current_tags.append(tag_lower)

    def handle_endtag(self, tag: str) -> None:
        if self._current_tags and self._current_tags[-1] == tag.lower():
            self._current_tags.pop()

    def handle_data(self, data: str) -> None:
        # Ignore data if inside script/style etc.
        if set(self._current_tags) & self._ignore_tags:
            return

        stripped = data.strip()
        if stripped:
            self._text_parts.append(stripped)

    def get_text(self) -> str:
        """Get the cleaned, extracted text."""
        # Join with a single space and clean up multiple spaces
        text = " ".join(self._text_parts)
        return re.sub(r"\s+", " ", text).strip()


class HtmlParser(ParserBase):
    """Parser for HTML documents.

    Reads the file as UTF-8 HTML and extracts clean text by stripping
    out structural tags, scripts, and stylesheets. Returns a single
    :class:`Section` containing the extracted text.
    """

    supported_media_types: list[str] = ["text/html"]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        return [".htm", ".html"]

    def __init__(self, encoding: str = "utf-8") -> None:
        """Initialize the HTML parser.

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
        """Read HTML, extract text, and return a single :class:`Section`.

        Args:
            file (`bytes | str`):
                The file content.  ``bytes`` is decoded with the
                configured encoding.  ``str`` is disambiguated at
                runtime: if it names an existing file on disk the
                file is read and decoded; otherwise it is used
                verbatim as pre-decoded text.
            filename (`str`):
                The source filename, copied verbatim into
                :attr:`Section.source`.

        Returns:
            `list[Section]`:
                Always a one-element list containing the cleaned file
                contents.

        Raises:
            `ValueError`: If the bytes cannot be decoded or HTML parsing fails.
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
        elif isinstance(file, bytes):
            try:
                text = file.decode(self.encoding)
            except UnicodeDecodeError as e:
                raise ValueError(
                    f"Failed to decode {filename!r} as "
                    f"{self.encoding!r}: {e}",
                ) from e
        else:
            raise TypeError(
                f"Expected bytes or str, got {type(file).__name__}",
            )

        extractor = _HTMLTextExtractor()
        try:
            extractor.feed(text)
        except Exception as e:
            raise ValueError(
                f"Failed to parse HTML from {filename!r}: {e}",
            ) from e

        clean_text = extractor.get_text()

        return [
            Section(
                content=TextBlock(text=clean_text),
                source=filename,
                metadata={},
            ),
        ]
