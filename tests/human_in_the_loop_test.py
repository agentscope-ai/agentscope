# -*- coding: utf-8 -*-
"""Tests for human-in-the-loop tool execution behavior in `Toolkit`."""

from unittest import IsolatedAsyncioTestCase
from typing import AsyncGenerator, Callable, Any
from agentscope.message import ToolUseBlock, TextBlock
from agentscope.mcp import MCPClientBase
from agentscope.tool import Toolkit, ToolResponse


class DummyMCPTool:
    """A minimal MCP tool description used for testing."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description or name
        # Keep inputSchema minimal; only structure is required by the code.
        self.inputSchema: dict = {}


class DummyMCPClient(MCPClientBase):
    """A dummy MCP client that exposes in-memory tools for testing."""

    def __init__(self, tools: dict[str, Callable]) -> None:
        super().__init__(name="dummy_mcp")
        self._impls = tools
        self._tools = [
            DummyMCPTool(
                name=name,
                description=f"Dummy MCP tool {name}",
            )
            for name in tools.keys()
        ]

    async def list_tools(self) -> list[DummyMCPTool]:
        """Return the available dummy tools."""
        return self._tools

    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,  # pylint: disable=unused-argument
    ) -> Callable:
        """Return an async wrapper around the underlying implementation."""
        impl = self._impls[func_name]

        async def _func(**kwargs: Any) -> Callable:
            return impl(**kwargs)

        _func.__name__ = func_name  # type: ignore[attr-defined]
        return _func


class HumanInTheLoopTest(IsolatedAsyncioTestCase):
    """Unittests for human-in-the-loop flow in toolkit."""

    async def asyncSetUp(self) -> None:
        """Set up toolkit for each test."""
        self.toolkit = Toolkit()

    async def asyncTearDown(self) -> None:
        """Clean up after each test."""
        self.toolkit = None

    async def _collect_single_response(
        self,
        res: AsyncGenerator[ToolResponse, None],
    ) -> ToolResponse:
        """Collect a single `ToolResponse` from an async generator."""
        chunks = []
        async for chunk in res:
            chunks.append(chunk)
        # All tests in this file expect a single response chunk
        self.assertEqual(len(chunks), 1)
        return chunks[0]

    async def test_human_permit_agree_execution(self) -> None:
        """Human permits tool execution without modification."""

        def test_tool(message: str) -> ToolResponse:
            """A simple test tool."""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"tool_called_with:{message}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Always permit execution without modification."""
            # Ensure the tool call is received as expected
            self.assertEqual(tool_call["name"], "test_tool")
            self.assertEqual(tool_call["input"]["message"], "hello")
            return True

        self.toolkit.register_tool_function(
            test_tool,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="123",
            name="test_tool",
            input={"message": "hello"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="tool_called_with:hello",
                    ),
                ],
            ),
            chunk,
        )

    async def test_human_permit_reject_execution(self) -> None:
        """Human rejects tool execution."""
        call_count = {"value": 0}

        def test_tool(message: str) -> ToolResponse:
            """A simple test tool that should not be called."""
            call_count["value"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"should_not_appear:{message}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Always reject execution."""
            # Ensure original tool name is visible to the human
            self.assertEqual(tool_call["name"], "test_tool")
            return False

        self.toolkit.register_tool_function(
            test_tool,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="456",
            name="test_tool",
            input={"message": "hello"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # Underlying tool should not be called at all
        self.assertEqual(call_count["value"], 0)
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="Tool execution `test_tool` denied by user",
                    ),
                ],
            ),
            chunk,
        )

    async def test_human_permit_modify_tool_name_and_args(self) -> None:
        """Human modifies both tool name and its arguments before execution."""
        call_log = {"tool_one": 0, "tool_two": 0}

        def tool_one(message: str) -> ToolResponse:
            """Original tool that should be redirected."""
            call_log["tool_one"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"tool_one_called:{message}",
                    ),
                ],
            )

        def tool_two(code: str) -> ToolResponse:
            """Target tool after human modification."""
            call_log["tool_two"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"tool_two_called:{code}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Modify tool name and arguments in-place,
            then permit execution.
            The ToolUseBlock id should not be modified inplace."""
            # Check original call info
            self.assertEqual(tool_call["id"], "789")
            self.assertEqual(tool_call["name"], "tool_one")
            self.assertEqual(tool_call["input"]["message"], "original_message")

            # Redirect to another tool and modify arguments
            tool_call["name"] = "tool_two"
            tool_call["input"] = {"code": "modified_code"}
            return True

        self.toolkit.register_tool_function(
            tool_one,
            human_permit_func=human_permit_func,
        )
        self.toolkit.register_tool_function(tool_two)

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="789",
            name="tool_one",
            input={"message": "original_message"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # Ensure only the redirected tool is executed
        self.assertEqual(call_log["tool_one"], 0)
        self.assertEqual(call_log["tool_two"], 1)

        # The tool response should come from `tool_two` with modified args
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="tool_two_called:modified_code",
                    ),
                ],
            ),
            chunk,
        )

        # The original ToolUseBlock should be modified in-place
        self.assertEqual(tool_use_block["id"], "789")
        self.assertEqual(tool_use_block["name"], "tool_two")
        self.assertEqual(tool_use_block["input"], {"code": "modified_code"})

    async def test_human_permit_modify_args_only(self) -> None:
        """Human only modifies tool arguments before execution."""

        def test_tool(number: int) -> ToolResponse:
            """Tool that echoes the received number."""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"received_number:{number}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Modify only the arguments in-place."""
            self.assertEqual(tool_call["id"], "999")
            self.assertEqual(tool_call["name"], "test_tool")
            self.assertEqual(tool_call["input"]["number"], 1)

            # Change the argument while keeping the tool name unchanged
            tool_call["input"]["number"] = 2
            return True

        self.toolkit.register_tool_function(
            test_tool,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="999",
            name="test_tool",
            input={"number": 1},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # The tool should be called with the modified argument
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="received_number:2",
                    ),
                ],
            ),
            chunk,
        )

        # The ToolUseBlock id should not be modified inplace
        self.assertEqual(tool_use_block["id"], "999")
        # The ToolUseBlock arguments should be updated in-place
        self.assertEqual(tool_use_block["input"]["number"], 2)

    async def test_mcp_human_permit_agree_execution(self) -> None:
        """Human permits MCP tool execution without modification."""

        def mcp_test_tool(message: str) -> ToolResponse:
            """A dummy MCP-backed test tool."""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"mcp_tool_called_with:{message}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Always permit execution without modification."""
            self.assertEqual(tool_call["name"], "mcp_test_tool")
            self.assertEqual(tool_call["input"]["message"], "hello_mcp")
            return True

        mcp_client = DummyMCPClient(
            tools={"mcp_test_tool": mcp_test_tool},
        )
        await self.toolkit.register_mcp_client(
            mcp_client,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="mcp-123",
            name="mcp_test_tool",
            input={"message": "hello_mcp"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="mcp_tool_called_with:hello_mcp",
                    ),
                ],
            ),
            chunk,
        )

    async def test_mcp_human_permit_reject_execution(self) -> None:
        """Human rejects MCP tool execution."""
        call_count = {"value": 0}

        def mcp_test_tool(message: str) -> ToolResponse:
            """A dummy MCP-backed test tool that should not be called."""
            call_count["value"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"mcp_should_not_appear:{message}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Always reject execution."""
            self.assertEqual(tool_call["name"], "mcp_test_tool")
            return False

        mcp_client = DummyMCPClient(
            tools={"mcp_test_tool": mcp_test_tool},
        )
        await self.toolkit.register_mcp_client(
            mcp_client,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="mcp-456",
            name="mcp_test_tool",
            input={"message": "hello_mcp"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # Underlying MCP tool should not be called at all
        self.assertEqual(call_count["value"], 0)
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="Tool execution `mcp_test_tool` denied by user",
                    ),
                ],
            ),
            chunk,
        )

    async def test_mcp_human_permit_modify_tool_name_and_args(self) -> None:
        """Human modifies MCP tool name and arguments before execution."""
        call_log = {"mcp_tool_one": 0, "mcp_tool_two": 0}

        def mcp_tool_one(message: str) -> ToolResponse:
            """Original MCP-backed tool that should be redirected."""
            call_log["mcp_tool_one"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"mcp_tool_one_called:{message}",
                    ),
                ],
            )

        def mcp_tool_two(code: str) -> ToolResponse:
            """Target MCP-backed tool after human modification."""
            call_log["mcp_tool_two"] += 1
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"mcp_tool_two_called:{code}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Modify tool name and arguments in-place,
            then permit execution."""
            # Check original call info
            self.assertEqual(tool_call["id"], "mcp-789")
            self.assertEqual(tool_call["name"], "mcp_tool_one")
            self.assertEqual(
                tool_call["input"]["message"],
                "original_mcp_message",
            )

            # Redirect to another MCP tool and modify arguments
            tool_call["name"] = "mcp_tool_two"
            tool_call["input"] = {"code": "modified_mcp_code"}
            return True

        mcp_client = DummyMCPClient(
            tools={
                "mcp_tool_one": mcp_tool_one,
                "mcp_tool_two": mcp_tool_two,
            },
        )
        await self.toolkit.register_mcp_client(
            mcp_client,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="mcp-789",
            name="mcp_tool_one",
            input={"message": "original_mcp_message"},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # Ensure only the redirected MCP tool is executed
        self.assertEqual(call_log["mcp_tool_one"], 0)
        self.assertEqual(call_log["mcp_tool_two"], 1)

        # The tool response should come from `mcp_tool_two` with modified args
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="mcp_tool_two_called:modified_mcp_code",
                    ),
                ],
            ),
            chunk,
        )

        # The original ToolUseBlock should be modified in-place
        self.assertEqual(tool_use_block["id"], "mcp-789")
        self.assertEqual(tool_use_block["name"], "mcp_tool_two")
        self.assertEqual(
            tool_use_block["input"],
            {"code": "modified_mcp_code"},
        )

    async def test_mcp_human_permit_modify_args_only(self) -> None:
        """Human only modifies MCP tool arguments before execution."""

        def mcp_test_tool(number: int) -> ToolResponse:
            """MCP-backed tool that echoes the received number."""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"mcp_received_number:{number}",
                    ),
                ],
            )

        def human_permit_func(tool_call: ToolUseBlock) -> bool:
            """Modify only the arguments in-place for MCP tool."""
            self.assertEqual(tool_call["id"], "mcp-999")
            self.assertEqual(tool_call["name"], "mcp_test_tool")
            self.assertEqual(tool_call["input"]["number"], 1)

            # Change the argument while keeping the tool name unchanged
            tool_call["input"]["number"] = 2
            return True

        mcp_client = DummyMCPClient(
            tools={"mcp_test_tool": mcp_test_tool},
        )
        await self.toolkit.register_mcp_client(
            mcp_client,
            human_permit_func=human_permit_func,
        )

        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="mcp-999",
            name="mcp_test_tool",
            input={"number": 1},
        )

        res = await self.toolkit.call_tool_function(tool_use_block)
        chunk = await self._collect_single_response(res)

        # The MCP tool should be called with the modified argument
        self.assertEqual(
            ToolResponse(
                id=chunk.id,
                content=[
                    TextBlock(
                        type="text",
                        text="mcp_received_number:2",
                    ),
                ],
            ),
            chunk,
        )

        # The ToolUseBlock id should not be modified inplace
        self.assertEqual(tool_use_block["id"], "mcp-999")
        # The ToolUseBlock arguments should be updated in-place
        self.assertEqual(tool_use_block["input"]["number"], 2)
