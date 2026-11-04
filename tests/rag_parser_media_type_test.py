# -*- coding: utf-8 -*-
"""Tests for the media-type sniffer shared by the document parsers.

These exercise the magic-number helper directly, so they need no parser and
no fixture files. Kept out of ``rag_parser_test.py`` on purpose: the sniffer
is a byte-level utility, not a parser.
"""
from unittest import TestCase

from agentscope.rag._parser._utils import _guess_image_media_type


class GuessImageMediaTypeTest(TestCase):
    """The magic-number sniffer must accept every legal header length."""

    def test_webp_header_is_sniffed(self) -> None:
        """A full RIFF/WebP container is reported as ``image/webp``."""
        data = b"RIFF" + (24).to_bytes(4, "little") + b"WEBP" + b"VP8 "
        self.assertEqual(_guess_image_media_type(data), "image/webp")

    def test_minimal_twelve_byte_webp_header(self) -> None:
        """The smallest legal 12-byte header is still a WebP.

        ``RIFF`` + a 4-byte size + ``WEBP`` is exactly 12 bytes, so a guard
        of ``> 12`` falls through and mislabels it as JPEG.
        """
        data = b"RIFF" + (4).to_bytes(4, "little") + b"WEBP"
        self.assertEqual(len(data), 12)
        self.assertEqual(_guess_image_media_type(data), "image/webp")

    def test_truncated_webp_is_not_claimed(self) -> None:
        """Too short to carry the ``WEBP`` tag, so it falls back to JPEG."""
        self.assertEqual(
            _guess_image_media_type(
                b"RIFF" + (0).to_bytes(4, "little") + b"WE",
            ),
            "image/jpeg",
        )

    def test_other_signatures_are_unaffected(self) -> None:
        """PNG, GIF and BMP keep their own media types."""
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
        self.assertEqual(_guess_image_media_type(png), "image/png")
        self.assertEqual(
            _guess_image_media_type(b"GIF89a" + b"0" * 16),
            "image/gif",
        )
        self.assertEqual(
            _guess_image_media_type(b"BM" + b"0" * 16),
            "image/bmp",
        )
        # A 12-byte JPEG-ish payload still falls back to JPEG.
        self.assertEqual(
            _guess_image_media_type(b"\xff\xd8\xff\xe0" + b"0" * 8),
            "image/jpeg",
        )
