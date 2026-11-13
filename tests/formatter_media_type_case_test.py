# -*- coding: utf-8 -*-
"""Unittests showing that tool-result media type matching must not depend on
the host platform."""
import os
import unittest
from unittest.mock import patch

from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import DataBlock, URLSource


def _data_block(media_type: str) -> DataBlock:
    """A remote image block, so nothing is written to disk here."""
    return DataBlock(
        source=URLSource(
            type="url",
            url="https://example.com/cat.png",
            media_type=media_type,
        ),
    )


class TestSupportedMediaTypeMatching(unittest.IsolatedAsyncioTestCase):
    """An uppercase media type is the same type as a lowercase one."""

    def setUp(self) -> None:
        """Use a formatter whose input list includes ``image/*``."""
        self.formatter = OpenAIChatFormatter()

    def _promoted(self, media_type: str) -> int:
        """Return how many data blocks the formatter hands to the model."""
        _, data = self.formatter.convert_tool_result_to_string(
            [_data_block(media_type)],
        )
        return len([b for b in data if isinstance(b, DataBlock)])

    def test_lowercase_media_type_is_promoted(self) -> None:
        """The already-correct case must keep working."""
        self.assertEqual(self._promoted("image/png"), 1)

    def test_uppercase_media_type_is_promoted(self) -> None:
        """The block must be handed to the model as an image, not dropped."""
        self.assertEqual(self._promoted("IMAGE/PNG"), 1)

    def test_mixed_case_media_type_is_promoted(self) -> None:
        """``Image/PNG`` is the same type as ``image/png``."""
        self.assertEqual(self._promoted("Image/PNG"), 1)

    def test_posix_platform_gives_the_same_answer(self) -> None:
        """`fnmatch` lowercases only on Windows, so this is the case that
        used to differ between laptops and CI containers."""
        with patch.object(os.path, "normcase", lambda name: name):
            self.assertEqual(self._promoted("Image/PNG"), 1)

    def test_unsupported_media_type_is_not_promoted(self) -> None:
        """A type outside the model's input list still falls back."""
        self.assertEqual(self._promoted("video/mp4"), 0)


if __name__ == "__main__":
    unittest.main()
