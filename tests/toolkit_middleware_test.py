# -*- coding: utf-8 -*-
"""The unittests for toolkit middleware."""
from typing import Callable, AsyncGenerator, Coroutine, Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolUseBlock
from agentscope.tool import Toolkit, ToolResponse


async def middleware_1(
    tool_call: ToolUseBlock,
    request: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> AsyncGenerator[ToolResponse, None]:
    """A simple middleware that adds a key-value pair to the kwargs."""
    # Pre-processing
    tool_call["input"]["a"] = "[pre1]" + str(tool_call["input"]["a"])

    async for chunk in await request(tool_call):
        chunk.content[0]["text"] += "[post1]"
        yield chunk


async def middleware_2(
    tool_call: ToolUseBlock,
    request: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> AsyncGenerator[ToolResponse, None]:
    """Another middleware that adds a key-value pair to the kwargs."""
    # Pre-processing
    tool_call["input"]["a"] = "[pre2]" + str(tool_call["input"]["a"])

    async for chunk in await request(tool_call):
        chunk.content[0]["text"] += "[post2]"
        yield chunk


async def tool(a: str) -> ToolResponse:
    """The test tool function.

    Args:
        a (`str`):
            A string input.

    Returns:
        `ToolResponse`:
            The tool response containing the input value.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=a)],
    )


class ToolkitMiddlewareTest(IsolatedAsyncioTestCase):
    """Test the toolkit middleware."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.toolkit = Toolkit()
        self.toolkit.register_tool_function(tool)

    async def test_toolkit_middleware(self) -> None:
        """Test the toolkit middleware."""
        self.toolkit.register_middleware(middleware_1)
        self.toolkit.register_middleware(middleware_2)

        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                name="tool",
                input={"a": "[ori]"},
                id="123",
            ),
        )

        async for chunk in res:
            print(chunk)
