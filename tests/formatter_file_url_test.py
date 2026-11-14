# -*- coding: utf-8 -*-
"""Tests for resolving ``file://`` URLs to local filesystem paths.

RFC 8089 gives ``file://`` URLs an *authority* component: ``file://host/…``
is a network path (a UNC share on Windows), while ``file:///…`` addresses
the local filesystem and ``localhost`` is defined to be equivalent to no
authority at all. Stripping the scheme with ``str.removeprefix("file://")``
loses the authority, which turns a network path into a relative host path.
"""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from urllib.request import url2pathname

from agentscope.formatter import OpenAIChatFormatter
from agentscope.formatter._formatter_base import file_url_to_path
from agentscope.message import DataBlock, TextBlock, URLSource, UserMsg


class TestFileUrlToPath(unittest.TestCase):
    """Unit tests for the shared ``file://`` URL resolver."""

    def test_authority_becomes_network_path(self) -> None:
        """A non-empty authority addresses a network host, not a relative
        path: ``file://fileserver/pub/img.png`` must keep the host."""
        self.assertEqual(
            file_url_to_path("file://fileserver/pub/img.png"),
            url2pathname("//fileserver/pub/img.png"),
        )

    def test_localhost_is_dropped(self) -> None:
        """RFC 8089: ``localhost`` is equivalent to the empty authority."""
        self.assertEqual(
            file_url_to_path("file://localhost/pub/img.png"),
            url2pathname("/pub/img.png"),
        )

    def test_local_path_unchanged(self) -> None:
        """The empty authority resolves on the local filesystem."""
        self.assertEqual(
            file_url_to_path("file:///pub/img.png"),
            url2pathname("/pub/img.png"),
        )

    def test_no_trailing_component_is_lost(self) -> None:
        """Round trip: resolving the URL of an existing file finds it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roundtrip.png"
            path.write_bytes(b"ping")
            url = path.as_uri()
            self.assertEqual(Path(file_url_to_path(url)), path)


class TestFormatterNetworkFileUrl(IsolatedAsyncioTestCase):
    """The formatters must read the file the URL addresses."""

    async def test_reads_local_file_via_localhost_authority(self) -> None:
        """``file://localhost/…`` is the spelled-out local form: the
        formatter must open the addressed file, not a relative path
        ``localhost/…`` under the current working directory."""
        payload = b"authority-aware-bytes"
        expected = (
            f"data:image/png;base64,{base64.b64encode(payload).decode('utf-8')}"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "img.png"
            path.write_bytes(payload)
            url = "file://localhost/" + path.as_posix().lstrip("/")
            res = await OpenAIChatFormatter().format(
                [
                    UserMsg(
                        name="user",
                        content=[
                            TextBlock(text="What's in this image?"),
                            DataBlock(
                                source=URLSource(url=url, media_type="image/png"),
                            ),
                        ],
                    ),
                ],
            )
        self.assertEqual(res[0]["content"][1]["image_url"]["url"], expected)


if __name__ == "__main__":
    unittest.main()
