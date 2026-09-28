# -*- coding: utf-8 -*-
"""WebSearch tool test case."""

import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.message import TextBlock
from agentscope.tool import WebSearch, ToolChunk
from agentscope.tool._builtin._web_search import ToolResultState

try:
    import duckduckgo_search  # noqa: F401 # pylint: disable=unused-import

    HAS_DUCKDUCKGO = True
except ImportError:
    HAS_DUCKDUCKGO = False


class WebSearchToolTest(IsolatedAsyncioTestCase):
    """The WebSearch tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.web_search_tool = WebSearch()

    async def test_tool_properties(self) -> None:
        """Test WebSearch tool properties."""
        self.assertEqual(self.web_search_tool.name, "WebSearch")
        self.assertIsInstance(self.web_search_tool.description, str)
        self.assertIsInstance(self.web_search_tool.input_schema, dict)
        self.assertFalse(self.web_search_tool.is_mcp)
        self.assertTrue(self.web_search_tool.is_read_only)

    @unittest.skipIf(not HAS_DUCKDUCKGO, "duckduckgo-search is not installed")
    @patch("duckduckgo_search.DDGS")
    async def test_search_with_results(self, mock_ddgs: MagicMock) -> None:
        """Test executing a search with valid results."""
        mock_instance = MagicMock()
        mock_instance.text.return_value = [
            {
                "title": "Test Title",
                "href": "https://example.com",
                "body": "Test Body",
            },
        ]
        mock_ddgs.return_value.__enter__.return_value = mock_instance

        chunk = await self.web_search_tool(
            query="test query",
            max_results=1,
        )

        self.assertIsInstance(chunk, ToolChunk)
        self.assertEqual(chunk.state, ToolResultState.SUCCESS)
        self.assertTrue(chunk.is_last)
        self.assertEqual(len(chunk.content), 1)
        self.assertIsInstance(chunk.content[0], TextBlock)
        self.assertIn("Test Title", chunk.content[0].text)

    @unittest.skipIf(HAS_DUCKDUCKGO, "duckduckgo-search is installed")
    async def test_search_without_dependency(self) -> None:
        """Test executing a search when duckduckgo-search is not installed."""
        chunk = await self.web_search_tool(query="test query")

        self.assertEqual(chunk.state, ToolResultState.ERROR)
        self.assertIn("not installed", chunk.content[0].text)
