# -*- coding: utf-8 -*-
"""The MCP client test module in agentscope."""
import asyncio
import time
from multiprocessing import Process
from unittest.async_case import IsolatedAsyncioTestCase

import mcp.types
from mcp.server import FastMCP
from mcp.types import EmbeddedResource, TextResourceContents

from agentscope.mcp import HttpStatelessClient, HttpStatefulClient
from agentscope.message import TextBlock, ToolUseBlock
from agentscope.tool import ToolResponse, Toolkit


async def tool_1(arg1: str, arg2: list[int]) -> str:
    """A test tool function.

    Args:
        arg1 (`str`):
            The first argument named arg1.
        arg2 (`list[int]`):
            The second argument named arg2.
    """
    return f"arg1: {arg1}, arg2: {arg2}"


async def tool_2() -> list:
    """
    A test tool function return the EmbeddedResource type
    """
    return [
        EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri="file://tmp.txt",
                mimeType="text/plain",
                text="test content",
            ),
        ),
    ]


async def slow_tool(delay: float = 3.0) -> str:
    """A slow tool that simulates timeout.

    Args:
        delay (`float`):
            Sleep duration in seconds.
    """
    await asyncio.sleep(delay)
    return f"Completed after {delay} seconds"


def setup_server() -> None:
    """Set up the streamable HTTP MCP server."""
    sse_server = FastMCP("StreamableHTTP", port=8002)
    sse_server.tool(description="A test tool function.")(tool_1)
    sse_server.tool(
        description="A test tool function with embedded resource.",
    )(tool_2)
    sse_server.tool(description="A slow tool for timeout testing.")(slow_tool)
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
        self.toolkit = Toolkit()

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

    async def test_embedded_content(self) -> None:
        """Test the EmbeddedContent functionality."""
        client = HttpStatelessClient(
            name="test_embedded_content",
            transport="streamable_http",
            url=f"http://127.0.0.1:{self.port}/mcp",
        )

        func_3 = await client.get_callable_function(
            "tool_2",
            wrap_tool_result=True,
        )
        res: ToolResponse = await func_3()
        self.assertEqual(
            res,
            ToolResponse(
                id=res.id,
                content=[
                    TextBlock(
                        type="text",
                        text="""{
  "uri": "file://tmp.txt/",
  "mimeType": "text/plain",
  "meta": null,
  "text": "test content"
}""",
                    ),
                ],
            ),
        )

    async def test_execution_timeout_with_register_mcp_client(self) -> None:
        """Test execution_timeout parameter in register_mcp_client."""
        stateless_client = HttpStatelessClient(
            name="test_timeout_stateless",
            transport="streamable_http",
            url=f"http://127.0.0.1:{self.port}/mcp",
        )

        # Register with execution_timeout=1.0 second
        await self.toolkit.register_mcp_client(
            stateless_client,
            execution_timeout=1.0,
        )

        # Call slow_tool should timeout in ~1 second
        start_time = time.time()
        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="timeout_test",
                type="tool_use",
                name="slow_tool",
                input={"delay": 3.0},
            ),
        )

        response_received = False
        async for chunk in res_gen:
            response_received = True
            self.assertIsInstance(chunk, ToolResponse)

        elapsed = time.time() - start_time
        self.assertTrue(response_received, "Should receive error response")
        # Should timeout around 1 second, allow 0.5s tolerance
        self.assertLess(elapsed, 2.0, f"Should timeout in ~1s, got {elapsed:.2f}s")
        self.assertGreater(elapsed, 0.5, f"Should take at least 0.5s, got {elapsed:.2f}s")

        # Test stateful client
        self.toolkit.clear()
        stateful_client = HttpStatefulClient(
            name="test_timeout_stateful",
            transport="streamable_http",
            url=f"http://127.0.0.1:{self.port}/mcp",
        )
        await stateful_client.connect()

        await self.toolkit.register_mcp_client(
            stateful_client,
            execution_timeout=1.0,
        )

        start_time = time.time()
        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="timeout_test_stateful",
                type="tool_use",
                name="slow_tool",
                input={"delay": 3.0},
            ),
        )

        response_received = False
        async for chunk in res_gen:
            response_received = True
            self.assertIsInstance(chunk, ToolResponse)

        elapsed = time.time() - start_time
        self.assertTrue(response_received, "Should receive error response")
        self.assertLess(elapsed, 2.0, f"Should timeout in ~1s, got {elapsed:.2f}s")
        self.assertGreater(elapsed, 0.5, f"Should take at least 0.5s, got {elapsed:.2f}s")

        await stateful_client.close()
