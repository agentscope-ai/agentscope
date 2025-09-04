# -*- coding: utf-8 -*-
"""The MCP client test module in agentscope."""
import asyncio
from multiprocessing import Process
from unittest.async_case import IsolatedAsyncioTestCase

import mcp.types
from mcp.server import FastMCP

from agentscope.mcp import HttpStatelessClient, HttpStatefulClient
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


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
    sse_server = FastMCP("StreamableHTTP", port=8002)
    sse_server.tool(description="A test tool function.")(tool_1)
    sse_server.run(transport="streamable-http")


class StreamableHttpMCPClientTest(IsolatedAsyncioTestCase):
    """Test class for streamable HTTP MCP client."""

    async def asyncTearDown(self) -> None:
        """Tear down the test environment."""
        while self.process.is_alive():
            self.process.terminate()
            await asyncio.sleep(5)

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.port = 8002
        self.process = Process(target=setup_server)
        self.process.start()
        await asyncio.sleep(10)

    async def test_streamable_http_stateless_client(self) -> None:
        """Test the MCP server connection functionality."""

        client = HttpStatelessClient(
            name="test_streamable_http_stateless_client",
            transport="streamable_http",
            url=f"http://127.0.0.1:{self.port}/mcp",
        )

        func_1 = await client.get_callable_function(
            "tool_1",
            wrap_tool_result=False,
        )
        res_1: mcp.types.CallToolResult = await func_1(
            arg1="123",
            arg2=[1, 2, 3],
        )
        self.assertEqual(
            res_1.content[0].text,
            "arg1: 123, arg2: [1, 2, 3]",
        )

        func_2 = await client.get_callable_function(
            "tool_1",
            wrap_tool_result=True,
        )
        res_2: ToolResponse = await func_2(arg1="345", arg2=[4, 5, 6])
        self.assertEqual(
            res_2,
            ToolResponse(
                id=res_2.id,
                content=[
                    TextBlock(
                        text="arg1: 345, arg2: [4, 5, 6]",
                        type="text",
                    ),
                ],
            ),
        )

        # Test stateful client connection
        client = HttpStatefulClient(
            name="test_streamable_http_stateless_client",
            transport="streamable_http",
            url=f"http://127.0.0.1:{self.port}/mcp",
        )

        self.assertFalse(client.is_connected)
        await client.connect()

        self.assertTrue(client.is_connected)

        func_1 = await client.get_callable_function(
            "tool_1",
            wrap_tool_result=False,
        )
        res_3: mcp.types.CallToolResult = await func_1(
            arg1="12",
            arg2=[1, 2],
        )
        self.assertEqual(
            res_3.content[0].text,
            "arg1: 12, arg2: [1, 2]",
        )

        func_2 = await client.get_callable_function(
            "tool_1",
            wrap_tool_result=True,
        )
        res_4: ToolResponse = await func_2(arg1="34", arg2=[4, 5])
        self.assertEqual(
            res_4,
            ToolResponse(
                id=res_4.id,
                content=[
                    TextBlock(
                        text="arg1: 34, arg2: [4, 5]",
                        type="text",
                    ),
                ],
            ),
        )

        await client.close()
        self.assertFalse(client.is_connected)
