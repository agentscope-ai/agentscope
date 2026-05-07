# -*- coding: utf-8 -*-
"""The MCP client test module in agentscope."""
import asyncio
from multiprocessing import Process
from unittest.async_case import IsolatedAsyncioTestCase

from mcp.server import FastMCP
from mcp.types import EmbeddedResource, TextResourceContents

from agentscope.mcp import MCPClient, HttpMCPConfig
from agentscope.tool import ToolChunk


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


def setup_server() -> None:
    """Set up the streamable HTTP MCP server."""
    sse_server = FastMCP("StreamableHTTP", port=8002)
    sse_server.tool(description="A test tool function.")(tool_1)
    sse_server.tool(
        description="A test tool function with embedded resource.",
    )(tool_2)
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

        # Test stateless client (is_stateful=False)
        client = MCPClient(
            name="test_streamable_http_stateless_client",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                type="http_mcp",
                url=f"http://127.0.0.1:{self.port}/mcp",
            ),
        )

        my_tool_1 = await client.get_tool("tool_1")
        res_1: ToolChunk = await my_tool_1(arg1="123", arg2=[1, 2, 3])
        self.assertEqual(
            res_1.content[0].text,
            "arg1: 123, arg2: [1, 2, 3]",
        )

        res_2: ToolChunk = await my_tool_1(arg1="345", arg2=[4, 5, 6])
        self.assertEqual(
            res_2.content[0].text,
            "arg1: 345, arg2: [4, 5, 6]",
        )

        # Test stateful client (is_stateful=True)
        client = MCPClient(
            name="test_streamable_http_stateful_client",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                type="http_mcp",
                url=f"http://127.0.0.1:{self.port}/mcp",
            ),
        )

        self.assertFalse(client.is_connected)
        await client.connect()

        self.assertTrue(client.is_connected)

        my_tool_1 = await client.get_tool("tool_1")
        res_3: ToolChunk = await my_tool_1(arg1="12", arg2=[1, 2])
        self.assertEqual(
            res_3.content[0].text,
            "arg1: 12, arg2: [1, 2]",
        )

        res_4: ToolChunk = await my_tool_1(arg1="34", arg2=[4, 5])
        self.assertEqual(
            res_4.content[0].text,
            "arg1: 34, arg2: [4, 5]",
        )

        await client.close()
        self.assertFalse(client.is_connected)

    async def test_embedded_content(self) -> None:
        """Test the EmbeddedContent functionality."""
        # Test with stateless client (is_stateful=False)
        client = MCPClient(
            name="test_embedded_content",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                type="http_mcp",
                url=f"http://127.0.0.1:{self.port}/mcp",
            ),
        )

        my_tool_2 = await client.get_tool("tool_2")
        res: ToolChunk = await my_tool_2()
        self.assertEqual(
            res.content[0].text,
            """{
  "uri": "file://tmp.txt/",
  "mimeType": "text/plain",
  "meta": null,
  "text": "test content"
}""",
        )
