# -*- coding: utf-8 -*-
"""Gateway requests retain actor/session MCP scope."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import json
from unittest import IsolatedAsyncioTestCase

import httpx
import mcp.types

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk
from agentscope.workspace._gateway_client import GatewayMCPTool


class TestGatewayMCPToolScope(IsolatedAsyncioTestCase):
    async def test_call_forwards_actor_and_session(self) -> None:
        captured: httpx.Request | None = None
        chunk = ToolChunk(
            content=[TextBlock(text="ok")],
            state=ToolResultState.SUCCESS,
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured
            captured = request
            return httpx.Response(
                200,
                json={"chunk": chunk.model_dump(mode="json")},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            tool = GatewayMCPTool(
                mcp_name="files",
                tool=mcp.types.Tool(
                    name="read",
                    inputSchema={"type": "object"},
                ),
                gateway_url="http://gateway",
                token="token",
                http=client,
                agent_id="agent-1",
                session_id="session-1",
            )
            result = await tool(path="a.txt")

        self.assertEqual(result.state, ToolResultState.SUCCESS)
        assert captured is not None
        self.assertEqual(captured.url.params["agent_id"], "agent-1")
        self.assertEqual(captured.url.params["session_id"], "session-1")
        self.assertEqual(
            json.loads(captured.content),
            {"arguments": {"path": "a.txt"}},
        )
