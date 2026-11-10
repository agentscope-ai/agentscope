# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OTel GenAI modality derivation from MIME types."""
from unittest import TestCase

from agentscope.middleware._tracing._converter import _get_modality


class GetModalityTest(TestCase):
    """MIME media types are case-insensitive and may carry parameters."""

    def test_lowercase_media_types_keep_their_modality(self) -> None:
        """Ordinary lowercase MIME types map to their OTel modality."""
        self.assertEqual(_get_modality("image/png"), "image")
        self.assertEqual(_get_modality("audio/mpeg"), "audio")
        self.assertEqual(_get_modality("video/mp4"), "video")
        self.assertEqual(_get_modality("application/pdf"), "unknown")
        self.assertEqual(_get_modality(""), "unknown")

    def test_mime_types_are_case_insensitive(self) -> None:
        """RFC 2045 media types are case-insensitive."""
        self.assertEqual(_get_modality("IMAGE/PNG"), "image")
        self.assertEqual(_get_modality("Image/Png"), "image")
        self.assertEqual(_get_modality("Audio/MPEG"), "audio")
        self.assertEqual(_get_modality("VIDEO/mp4"), "video")

    def test_surrounding_whitespace_is_ignored(self) -> None:
        """Optional whitespace around the media type is tolerated."""
        self.assertEqual(_get_modality(" image/png "), "image")
        self.assertEqual(_get_modality("\taudio/wav\n"), "audio")

    def test_media_type_parameters_are_ignored(self) -> None:
        """A media type with parameters still yields its modality."""
        self.assertEqual(
            _get_modality("IMAGE/PNG; charset=utf-8"),
            "image",
        )
        self.assertEqual(
            _get_modality("audio/mp3; rate=44100"),
            "audio",
        )
