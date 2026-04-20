# -*- coding: utf-8 -*-
"""The MCP client test module in agentscope."""
import asyncio
import json
from multiprocessing import Process
from unittest.async_case import IsolatedAsyncioTestCase

from mcp.server import FastMCP

from agentscope.mcp import HttpStatelessClient, HttpStatefulClient
from agentscope.message import ToolCallBlock
from agentscope.tool import ToolResponse, ToolChunk, Toolkit
from agentscope.agent import AgentState


async def tool_1(arg1: str, arg2: list[int]) -> str:
    """A test tool function.

    Args:
        arg1 (`str`):
            The first argument named arg1.
        arg2 (`list[int]`):
            The second argument named arg2.
    """
    return f"arg1: {arg1}, arg2: {arg2}"


def setup_server() -> None:
    """Set up the streamable HTTP MCP server."""
    sse_server = FastMCP("SSE", port=8003)
    sse_server.tool(description="A test tool function.")(tool_1)
    sse_server.run(transport="sse")


class SseMCPClientTest(IsolatedAsyncioTestCase):
    """Test class for MCP server functionality."""

    async def asyncTearDown(self) -> None:
        """Tear down the test environment."""
        del self.toolkit

        while self.process.is_alive():
            self.process.terminate()
            await asyncio.sleep(5)

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.port = 8003
        self.process = Process(target=setup_server)
        self.process.start()
        await asyncio.sleep(10)

        self.toolkit = Toolkit()
        self.schemas = [
            {
                "type": "function",
                "function": {
                    "name": "tool_1",
                    "description": "A test tool function.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "arg1": {
                                "type": "string",
                            },
                            "arg2": {
                                "items": {
                                    "type": "integer",
                                },
                                "type": "array",
                            },
                        },
                        "required": [
                            "arg1",
                            "arg2",
                        ],
                    },
                },
            },
        ]

    async def test_stateless_client(self) -> None:
        """Test the stateless sse MCP client."""
        stateless_client = HttpStatelessClient(
            name="test_sse_client",
            transport="sse",
            url=f"http://127.0.0.1:{self.port}/sse",
        )

        mcp_tool_1 = await stateless_client.get_tool("tool_1")
        # Repeat to ensure idempotency
        res_1: ToolChunk = await mcp_tool_1(arg1="123", arg2=[1, 2, 3])
        res_2: ToolChunk = await mcp_tool_1(arg1="345", arg2=[4, 5, 6])
        res_3: ToolChunk = await mcp_tool_1(arg1="345", arg2=[4, 5, 6])

        self.assertEqual(
            res_1.content[0].text,
            "arg1: 123, arg2: [1, 2, 3]",
        )
        self.assertEqual(
            res_2.content[0].text,
            "arg1: 345, arg2: [4, 5, 6]",
        )
        self.assertEqual(
            res_3.content[0].text,
            "arg1: 345, arg2: [4, 5, 6]",
        )

        # Register MCPTool via Toolkit constructor
        toolkit_with_mcp = Toolkit(tools=[mcp_tool_1])

        schemas = toolkit_with_mcp.get_function_schemas()
        self.assertListEqual(
            schemas,
            self.schemas,
        )

        state = AgentState()
        res_gen = toolkit_with_mcp.call_tool(
            ToolCallBlock(
                id="xx",
                type="tool_call",
                name="tool_1",
                input=json.dumps(
                    {
                        "arg1": "789",
                        "arg2": [7, 8, 9],
                    },
                ),
            ),
            state=state,
        )

        final_response = None
        async for chunk in res_gen:
            if isinstance(chunk, ToolResponse):
                final_response = chunk
            else:
                self.assertIsInstance(chunk, ToolChunk)

        self.assertIsNotNone(final_response)
        self.assertEqual(
            final_response.content[0].text,
            "arg1: 789, arg2: [7, 8, 9]",
        )

        self.toolkit.clear()
        self.assertDictEqual(self.toolkit.tools, {})

        # Try to add the mcp client
        await self.toolkit.register_mcp_client(stateless_client)
        self.assertListEqual(
            self.toolkit.get_function_schemas(),
            self.schemas,
        )

        self.toolkit.clear()

    async def test_stateful_client(self) -> None:
        """Test the stateful sse MCP client."""

        # Test stateful client
        stateful_client = HttpStatefulClient(
            name="test_sse_client_stateful",
            transport="sse",
            url=f"http://127.0.0.1:{self.port}/sse",
        )

        self.assertFalse(stateful_client.is_connected)
        await stateful_client.connect()

        self.assertTrue(stateful_client.is_connected)

        mcp_tool_1 = await stateful_client.get_tool("tool_1")
        # Repeat to ensure idempotency
        res_1: ToolChunk = await mcp_tool_1(arg1="12", arg2=[1, 2])
        res_2: ToolChunk = await mcp_tool_1(arg1="34", arg2=[4, 5])
        res_3: ToolChunk = await mcp_tool_1(arg1="34", arg2=[4, 5])

        self.assertEqual(
            res_1.content[0].text,
            "arg1: 12, arg2: [1, 2]",
        )
        self.assertEqual(
            res_2.content[0].text,
            "arg1: 34, arg2: [4, 5]",
        )
        self.assertEqual(
            res_3.content[0].text,
            "arg1: 34, arg2: [4, 5]",
        )

        # with toolkit - Register MCPTool via Toolkit constructor
        toolkit_with_mcp = Toolkit(tools=[mcp_tool_1])

        self.assertListEqual(
            toolkit_with_mcp.get_function_schemas(),
            self.schemas,
        )

        state = AgentState()
        res_gen = toolkit_with_mcp.call_tool(
            ToolCallBlock(
                id="xx",
                type="tool_call",
                name="tool_1",
                input=json.dumps(
                    {
                        "arg1": "56",
                        "arg2": [5, 6],
                    },
                ),
            ),
            state=state,
        )

        final_response = None
        async for chunk in res_gen:
            if isinstance(chunk, ToolResponse):
                final_response = chunk
            else:
                self.assertIsInstance(chunk, ToolChunk)

        self.assertIsNotNone(final_response)
        self.assertEqual(
            final_response.content[0].text,
            "arg1: 56, arg2: [5, 6]",
        )

        # mcp client level test
        self.toolkit.clear()
        self.assertDictEqual(self.toolkit.tools, {})

        await self.toolkit.register_mcp_client(stateful_client)
        self.assertListEqual(
            self.toolkit.get_function_schemas(),
            self.schemas,
        )

        await stateful_client.close()
        self.assertFalse(stateful_client.is_connected)
