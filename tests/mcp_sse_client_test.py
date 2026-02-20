# -*- coding: utf-8 -*-
"""The MCP client test module in agentscope."""
import asyncio
import time
from multiprocessing import Process
from unittest.async_case import IsolatedAsyncioTestCase

import mcp.types
from mcp.server import FastMCP

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
    sse_server = FastMCP("SSE", port=8003)
    sse_server.tool(description="A test tool function.")(tool_1)
    sse_server.tool(description="A slow tool for timeout testing.")(slow_tool)
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
        self.schemas_wo_arg1 = [
            {
                "type": "function",
                "function": {
                    "name": "tool_1",
                    "description": "A test tool function.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "arg2": {
                                "items": {
                                    "type": "integer",
                                },
                                "title": "Arg2",
                                "type": "array",
                            },
                        },
                        "required": [
                            "arg2",
                        ],
                    },
                },
            },
        ]
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
                                "title": "Arg1",
                                "type": "string",
                            },
                            "arg2": {
                                "items": {
                                    "type": "integer",
                                },
                                "title": "Arg2",
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

        func_1 = await stateless_client.get_callable_function(
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

        func_2 = await stateless_client.get_callable_function(
            "tool_1",
            wrap_tool_result=True,
        )
        # Repeat to ensure idempotency
        res_2: ToolResponse = await func_2(arg1="345", arg2=[4, 5, 6])
        res_3: ToolResponse = await func_2(arg1="345", arg2=[4, 5, 6])
        res_4: ToolResponse = await func_2(arg1="345", arg2=[4, 5, 6])
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
        self.assertEqual(
            res_3,
            ToolResponse(
                id=res_3.id,
                content=[
                    TextBlock(
                        text="arg1: 345, arg2: [4, 5, 6]",
                        type="text",
                    ),
                ],
            ),
        )
        self.assertEqual(
            res_4,
            ToolResponse(
                id=res_4.id,
                content=[
                    TextBlock(
                        text="arg1: 345, arg2: [4, 5, 6]",
                        type="text",
                    ),
                ],
            ),
        )

        self.toolkit.register_tool_function(
            func_2,
        )

        schemas = self.toolkit.get_json_schemas()
        self.assertListEqual(
            schemas,
            self.schemas,
        )

        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="xx",
                type="tool_use",
                name="tool_1",
                input={
                    "arg1": "789",
                    "arg2": [7, 8, 9],
                },
            ),
        )

        async for chunk in res_gen:
            self.assertEqual(
                chunk,
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            text="arg1: 789, arg2: [7, 8, 9]",
                            type="text",
                        ),
                    ],
                ),
            )

        self.toolkit.clear()
        self.assertDictEqual(self.toolkit.tools, {})

        # Try to add the mcp client
        await self.toolkit.register_mcp_client(
            stateless_client,
            disable_funcs=["slow_tool"],
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            self.schemas,
        )

        self.toolkit.clear()
        await self.toolkit.register_mcp_client(
            stateless_client,
            disable_funcs=["slow_tool"],
            preset_kwargs_mapping={
                "tool_1": {
                    "arg1": "default_value",
                },
            },
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            self.schemas_wo_arg1,
        )
        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="xx",
                type="tool_use",
                name="tool_1",
                input={
                    "arg2": [11, 12],
                },
            ),
        )
        async for chunk in res_gen:
            self.assertEqual(
                chunk,
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            text="arg1: default_value, arg2: [11, 12]",
                            type="text",
                        ),
                    ],
                ),
            )

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

        func_1 = await stateful_client.get_callable_function(
            "tool_1",
            wrap_tool_result=False,
        )
        res_1: mcp.types.CallToolResult = await func_1(
            arg1="12",
            arg2=[1, 2],
        )
        self.assertEqual(
            res_1.content[0].text,
            "arg1: 12, arg2: [1, 2]",
        )

        func_2 = await stateful_client.get_callable_function(
            "tool_1",
            wrap_tool_result=True,
        )
        res_2: ToolResponse = await func_2(arg1="34", arg2=[4, 5])
        res_3: ToolResponse = await func_2(arg1="34", arg2=[4, 5])
        res_4: ToolResponse = await func_2(arg1="34", arg2=[4, 5])
        self.assertEqual(
            res_2,
            ToolResponse(
                id=res_2.id,
                content=[
                    TextBlock(
                        text="arg1: 34, arg2: [4, 5]",
                        type="text",
                    ),
                ],
            ),
        )
        self.assertEqual(
            res_3,
            ToolResponse(
                id=res_3.id,
                content=[
                    TextBlock(
                        text="arg1: 34, arg2: [4, 5]",
                        type="text",
                    ),
                ],
            ),
        )
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

        # with toolkit
        self.toolkit.register_tool_function(func_2)
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            self.schemas,
        )

        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="xx",
                type="tool_use",
                name="tool_1",
                input={
                    "arg1": "56",
                    "arg2": [5, 6],
                },
            ),
        )
        async for chunk in res_gen:
            self.assertEqual(
                chunk,
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            text="arg1: 56, arg2: [5, 6]",
                            type="text",
                        ),
                    ],
                ),
            )

        # mcp client level test
        self.toolkit.clear()
        self.assertDictEqual(self.toolkit.tools, {})

        await self.toolkit.register_mcp_client(
            stateful_client,
            disable_funcs=["slow_tool"],
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            self.schemas,
        )

        self.toolkit.clear()
        await self.toolkit.register_mcp_client(
            stateful_client,
            disable_funcs=["slow_tool"],
            preset_kwargs_mapping={
                "tool_1": {
                    "arg1": "default_value",
                },
            },
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            self.schemas_wo_arg1,
        )
        res_gen = await self.toolkit.call_tool_function(
            ToolUseBlock(
                id="xx",
                type="tool_use",
                name="tool_1",
                input={
                    "arg2": [11, 12],
                },
            ),
        )
        async for chunk in res_gen:
            self.assertEqual(
                chunk,
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            text="arg1: default_value, arg2: [11, 12]",
                            type="text",
                        ),
                    ],
                ),
            )

        await stateful_client.close()
        self.assertFalse(stateful_client.is_connected)

    async def test_execution_timeout_with_register_mcp_client(self) -> None:
        """Test execution_timeout parameter in register_mcp_client."""
        stateless_client = HttpStatelessClient(
            name="test_timeout_stateless",
            transport="sse",
            url=f"http://127.0.0.1:{self.port}/sse",
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
            transport="sse",
            url=f"http://127.0.0.1:{self.port}/sse",
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

