# -*- coding: utf-8 -*-
"""MCP tool name composition tests."""
import unittest
from typing import Any

from mcp import types

from agentscope.tool import MCPTool


def _tool(name: str) -> types.Tool:
    """Build a minimal MCP tool descriptor."""
    return types.Tool(
        name=name,
        description="A tool.",
        inputSchema={"type": "object", "properties": {}},
    )


class _RecordingSession:
    """A session that records the names the server is asked for."""

    def __init__(self) -> None:
        """Start with no recorded call."""
        self.calls: list[str] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        read_timeout_seconds: float | None = None,
        progress_callback: Any | None = None,
    ) -> types.CallToolResult:
        """Record the call and return a plain text result."""
        del arguments, read_timeout_seconds, progress_callback
        self.calls.append(name)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")],
        )


def _mcp_tool(server: str, tool_name: str) -> MCPTool:
    """Wrap the given MCP tool under the given server name."""
    return MCPTool(
        mcp_name=server,
        tool=_tool(tool_name),
        session=_RecordingSession(),
    )


class MCPToolNameTest(unittest.IsolatedAsyncioTestCase):
    """The composed name is what the model sees, so it must be one the
    providers accept."""

    async def test_short_name_is_composed_as_before(self) -> None:
        """A short, already-legal name keeps the documented shape."""
        tool = _mcp_tool("git", "status")
        self.assertEqual(tool.name, "mcp__git__status")

    async def test_illegal_characters_are_replaced(self) -> None:
        """Characters outside the provider alphabet become "x"."""
        tool = _mcp_tool("notion", "search.docs")
        self.assertEqual(tool.name, "mcp__notion__searchxdocs")

    async def test_long_name_is_shortened_to_the_provider_limit(self) -> None:
        """A name over the limit makes the provider reject the whole
        request, so it is cut to the limit and stays readable."""
        tool_name = (
            "search_all_documents_in_the_enterprise_knowledge_base"
            "_with_filters"
        )
        tool = _mcp_tool("knowledge", tool_name)

        self.assertEqual(len(tool.name), 64)
        self.assertTrue(tool.name.startswith("mcp__knowledge__search_all"))
        self.assertRegex(tool.name, r"^[a-zA-Z0-9_-]{1,64}$")

    async def test_shortened_name_still_calls_the_declared_tool(self) -> None:
        """Shortening is only about what the model sees; the server still
        receives the name it declared."""
        tool_name = (
            "search_all_documents_in_the_enterprise_knowledge_base"
            "_with_filters"
        )
        session = _RecordingSession()
        tool = MCPTool(
            mcp_name="knowledge",
            tool=_tool(tool_name),
            session=session,
        )
        await tool()
        self.assertEqual(session.calls, [tool_name])

    async def test_long_names_sharing_a_prefix_stay_distinct(self) -> None:
        """Shortening must not collapse two tools of one server into one."""
        first = _mcp_tool(
            "knowledge",
            "search_all_documents_in_the_enterprise_knowledge_base_by_title",
        )
        second = _mcp_tool(
            "knowledge",
            "search_all_documents_in_the_enterprise_knowledge_base_by_author",
        )

        self.assertEqual(len(first.name), 64)
        self.assertEqual(len(second.name), 64)
        self.assertNotEqual(first.name, second.name)

    async def test_shortening_is_deterministic(self) -> None:
        """Formatting the same tool twice must yield the same name, so a
        stored session and its permission rules keep matching."""
        tool_name = "fetch_the_monthly_revenue_report_for_the_given_region"
        self.assertEqual(
            _mcp_tool("finance", tool_name).name,
            _mcp_tool("finance", tool_name).name,
        )

    async def test_long_server_name_alone_is_shortened(self) -> None:
        """The server name counts towards the limit too."""
        tool = _mcp_tool("a" * 60, "b" * 20)
        self.assertEqual(len(tool.name), 64)
        self.assertRegex(tool.name, r"^[a-zA-Z0-9_-]{1,64}$")


if __name__ == "__main__":
    unittest.main()
