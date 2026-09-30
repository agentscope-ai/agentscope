# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`FormatterBase`."""

import base64
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import agentscope.formatter._formatter_base as formatter_base
from agentscope.formatter._formatter_base import (
    FormatterBase,
    _cleanup_unsupported_media_temp_files,
)
from agentscope.message import Base64Source, DataBlock


class _TextOnlyFormatter(FormatterBase):
    """Formatter that sends every media block through the fallback path."""

    async def format(
        self,
        *args: object,
        **kwargs: object,
    ) -> list[dict]:
        """Implement the abstract formatter interface for unit tests."""
        return []


class FormatterBaseUnsupportedMediaTest(unittest.TestCase):
    """Unsupported-media Base64 storage regressions for issue #2173."""

    def setUp(self) -> None:
        """Create a text-only formatter and base64 fixtures."""
        _cleanup_unsupported_media_temp_files()
        self._formatter = _TextOnlyFormatter(input_types=["text/plain"])
        self._video_bytes = b"fake video payload"
        self._video_b64 = base64.b64encode(self._video_bytes).decode("ascii")
        self._binary_bytes = b"another binary payload"
        self._binary_b64 = base64.b64encode(self._binary_bytes).decode("ascii")

    def tearDown(self) -> None:
        """Remove process-owned fallback files between tests."""
        _cleanup_unsupported_media_temp_files()

    @staticmethod
    def _blocks(data: str, media_type: str) -> list[DataBlock]:
        """Build one unsupported-media block."""
        return [
            DataBlock(
                source=Base64Source(
                    data=data,
                    media_type=media_type,
                ),
            ),
        ]

    @staticmethod
    def _saved_path(text: str) -> str:
        """Extract the saved file path from a formatter reminder."""
        prefix = "saved locally at: "
        start = text.index(prefix) + len(prefix)
        return text[start : text.index(".</system-reminder>", start)]

    def test_same_base64_yields_deterministic_path(self) -> None:
        """Identical Base64 input must produce identical output and path."""
        blocks = self._blocks(self._video_b64, "video/mp4")

        first, _ = self._formatter.convert_tool_result_to_string(blocks)
        second, _ = self._formatter.convert_tool_result_to_string(blocks)

        self.assertEqual(first, second)
        self.assertEqual(self._saved_path(first), self._saved_path(second))

    def test_written_file_contains_decoded_bytes(self) -> None:
        """The fallback file must contain the decoded payload."""
        blocks = self._blocks(self._binary_b64, "application/x-binary")

        text, _ = self._formatter.convert_tool_result_to_string(blocks)

        with open(self._saved_path(text), "rb") as file:
            self.assertEqual(file.read(), self._binary_bytes)

    def test_distinct_contents_yield_distinct_paths(self) -> None:
        """Different payloads must use different stable paths."""
        first, _ = self._formatter.convert_tool_result_to_string(
            self._blocks(self._video_b64, "video/mp4"),
        )
        second, _ = self._formatter.convert_tool_result_to_string(
            self._blocks(self._binary_b64, "video/mp4"),
        )

        self.assertNotEqual(
            self._saved_path(first),
            self._saved_path(second),
        )

    def test_cache_directory_is_private_and_cleaned(self) -> None:
        """The cache must be process-private and removable by cleanup."""
        text, _ = self._formatter.convert_tool_result_to_string(
            self._blocks(self._video_b64, "video/mp4"),
        )
        path = self._saved_path(text)
        temp_dir = os.path.dirname(path)

        if os.name != "nt":
            self.assertEqual(
                stat.S_IMODE(os.stat(temp_dir).st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(os.stat(path).st_mode),
                0o600,
            )

        _cleanup_unsupported_media_temp_files()
        self.assertFalse(os.path.exists(temp_dir))

    def test_repeated_calls_leave_one_payload_file(self) -> None:
        """Repeated formatting must reuse one fallback file."""
        blocks = self._blocks(self._video_b64, "video/mp4")
        text = ""
        for _ in range(5):
            text, _ = self._formatter.convert_tool_result_to_string(blocks)

        temp_dir = os.path.dirname(self._saved_path(text))
        self.assertEqual(len(os.listdir(temp_dir)), 1)

    def test_cache_hit_skips_redundant_base64_decode(self) -> None:
        """A cache hit must not decode the payload again."""
        blocks = self._blocks(self._video_b64, "video/mp4")
        first, _ = self._formatter.convert_tool_result_to_string(blocks)

        with patch.object(
            formatter_base.base64,
            "b64decode",
            side_effect=AssertionError("unexpected decode"),
        ):
            second, _ = self._formatter.convert_tool_result_to_string(blocks)

        self.assertEqual(first, second)

    def test_replace_failure_is_propagated_without_invalid_path(self) -> None:
        """A failed atomic write must not return a missing target path."""
        blocks = self._blocks(self._video_b64, "video/mp4")

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "agentscope.formatter._formatter_base."
                    "_get_unsupported_media_temp_dir",
                    return_value=temp_dir,
                ),
                patch.object(
                    formatter_base.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    self._formatter.convert_tool_result_to_string(blocks)

            self.assertEqual(os.listdir(temp_dir), [])

    def test_worker_exit_does_not_remove_another_worker_file(self) -> None:
        """Each process must clean only its own unsupported-media cache."""
        script = """
import base64
import sys

from agentscope.formatter._formatter_base import FormatterBase
from agentscope.message import Base64Source, DataBlock


class TextOnlyFormatter(FormatterBase):
    async def format(self, *args, **kwargs):
        return []


payload = base64.b64encode(b"shared payload").decode("ascii")
text, _ = TextOnlyFormatter().convert_tool_result_to_string(
    [
        DataBlock(
            source=Base64Source(
                data=payload,
                media_type="video/mp4",
            ),
        ),
    ],
)
path = text.split("saved locally at: ", 1)[1].split(
    ".</system-reminder>",
    1,
)[0]
print(path, flush=True)
sys.stdin.readline()
"""
        with ExitStack() as stack:
            workers = [
                stack.enter_context(
                    subprocess.Popen(
                        [sys.executable, "-c", script],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    ),
                )
                for _ in range(2)
            ]
            paths: list[str] = []
            try:
                for worker in workers:
                    self.assertIsNotNone(worker.stdout)
                    paths.append(worker.stdout.readline().strip())

                self.assertNotEqual(
                    os.path.dirname(paths[0]),
                    os.path.dirname(paths[1]),
                )
                self.assertTrue(all(os.path.isfile(path) for path in paths))

                self.assertIsNotNone(workers[0].stdin)
                workers[0].stdin.close()
                self.assertEqual(workers[0].wait(timeout=20), 0)
                self.assertFalse(os.path.exists(paths[0]))
                self.assertTrue(os.path.isfile(paths[1]))
            finally:
                for worker in workers:
                    if worker.poll() is None:
                        self.assertIsNotNone(worker.stdin)
                        worker.stdin.close()
                        worker.wait(timeout=20)


if __name__ == "__main__":
    unittest.main()
