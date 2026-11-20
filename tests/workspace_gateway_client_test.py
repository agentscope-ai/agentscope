# -*- coding: utf-8 -*-
"""Tests for the host-side MCP gateway client."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import mcp.types

from agentscope.workspace._gateway_client import GatewayClient, GatewayMCPTool

_SUCCESS_BODY = json.dumps(
    {"chunk": {"content": [{"type": "text", "text": "ok"}]}},
)


class GatewayMCPToolNameTest(IsolatedAsyncioTestCase):
    """The provider-visible name vs. the upstream name it dispatches to."""

    def test_upstream_tool_name_is_sanitized(self) -> None:
        """An MCP server may declare names providers reject.

        The upstream name is composed into ``mcp__<server>__<tool>``,
        and providers require that whole name to match
        ``^[a-zA-Z0-9_-]+$``, so dots and colons cannot pass through.
        """
        tool = GatewayMCPTool(
            mcp_name="files",
            tool=mcp.types.Tool(
                name="read.file:v2",
                inputSchema={"type": "object"},
            ),
            gateway=Mock(),
        )

        self.assertEqual(tool.name, "mcp__files__readxfilexv2")
        self.assertRegex(tool.name, r"^[a-zA-Z0-9_-]+$")

    async def test_call_targets_the_upstream_tool_name(self) -> None:
        """The gateway still receives the name the MCP server declared."""
        tool = GatewayMCPTool(
            mcp_name="files",
            tool=mcp.types.Tool(
                name="read.file",
                inputSchema={"type": "object"},
            ),
            gateway=GatewayClient(backend=Mock(), gateway_port=9000),
        )

        with patch.object(
            GatewayClient,
            "exec_request",
            new=AsyncMock(return_value=(200, _SUCCESS_BODY)),
        ) as exec_request:
            chunk = await tool(path="/tmp/a")

        self.assertEqual(chunk.content[0].text, "ok")
        self.assertEqual(
            exec_request.await_args.args[1],
            "/mcps/files/tools/read.file",
        )
