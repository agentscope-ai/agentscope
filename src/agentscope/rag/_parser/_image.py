# -*- coding: utf-8 -*-
"""Image file parser.

A single :class:`Section` carrying the raw image bytes as a
base64-encoded :class:`DataBlock`.  No OCR, no captioning — the
section is the image, ready to flow through to a multimodal
embedding model unchanged.
"""
import base64

from ...message import Base64Source, DataBlock
from .._document import Section
from ._base import ParserBase
from ._utils import _guess_image_media_type


class ImageParser(ParserBase):
    """Parser for image files.

    Wraps the entire file as a single :class:`Section` holding a
    :class:`DataBlock` with the image's base64-encoded bytes.  This is
    the input shape a multimodal embedding model expects; the
    surrounding pipeline (chunker, vector store) treats the section
    opaquely.

    The IANA media type is sniffed from the bytes' magic number so
    callers do not need to pass it explicitly.
    """

    supported_media_types: list[str] = [
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/bmp",
        "image/webp",
    ]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        """Return the canonical image extensions."""
        return [
            ".bmp",
            ".gif",
            ".jpeg",
            ".jpg",
            ".png",
            ".webp",
        ]

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Wrap the image bytes in a single :class:`Section`.

        Args:
            file (`bytes | str`):
                Either the raw image bytes, or a filesystem path to
                the image file.
            filename (`str`):
                The source filename, copied into
                :attr:`Section.source`.

        Returns:
            `list[Section]`:
                A one-element list whose section's ``content`` is a
                :class:`DataBlock` with the base64-encoded image data.

        Raises:
            `FileNotFoundError`: If ``file`` is a ``str`` pointing to
                a path that does not exist.
        """
        if isinstance(file, str):
            with open(file, "rb") as fp:
                file = fp.read()

        media_type = _guess_image_media_type(file)
        data = base64.b64encode(file).decode("utf-8")
        return [
            Section(
                content=DataBlock(
                    source=Base64Source(
                        media_type=media_type,
                        data=data,
                    ),
                    name=filename,
                ),
                source=filename,
                metadata={"media_type": media_type},
            ),
        ]
