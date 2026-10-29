# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position
"""Test diagnostic stdout routing for the ACP transport."""

import io
from unittest import TestCase
from unittest.mock import patch

import pytest

pytest.importorskip("acp", reason="ACP tests require the acp extra")

from agentscope.acp._stdio import _ProtocolStdout


class ACPStdioTest(TestCase):
    """Keep diagnostics separate from protocol output on each platform."""

    def test_binary_stdout_routing_matches_sdk_platform(self) -> None:
        """Only the Windows SDK needs access to protocol stdout.buffer."""
        for platform in ("darwin", "linux", "win32"):
            with self.subTest(platform=platform):
                protocol = io.BytesIO()
                errors = io.TextIOWrapper(io.BytesIO())
                with patch("sys.platform", platform), patch(
                    "sys.stderr",
                    errors,
                ):
                    stdout = _ProtocolStdout(protocol)
                    stdout.write("text diagnostic\n")
                    stdout.flush()
                    stdout.buffer.write(b"binary diagnostic\n")
                    self.assertEqual(
                        (protocol.getvalue(), errors.buffer.getvalue()),
                        (b"binary diagnostic\n", b"text diagnostic\n")
                        if platform == "win32"
                        else (b"", b"text diagnostic\nbinary diagnostic\n"),
                    )
