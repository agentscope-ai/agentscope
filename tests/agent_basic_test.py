# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""The basic test of the agent class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent
from agentscope.model import ChatResponse
from agentscope.tool import (
    ToolBase,
    Toolkit,
    ToolChunk,
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.tool._types import RegisteredTool


class MockSequentialTool(ToolBase):
    """A mock tool that is not concurrency safe (sequential execution)."""

    name: str = "mock_sequential_tool"
    description: str = "A mock sequential tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows",
            message="Mock tool always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Sequential result: {input}")],
        )


class MockConcurrentTool(ToolBase):
    """A mock tool that is concurrency safe (concurrent execution)."""

    name: str = "mock_concurrent_tool"
    description: str = "A mock concurrent tool for testing"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows",
            message="Mock tool always allows",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Concurrent result: {input}")],
        )


class AgentBasicTest(IsolatedAsyncioTestCase):
    """The basic test of the agent class."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.model = MockModel()
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(),
        )

    def _get_event_base(self, reply_id: str) -> dict:
        """Get the dict with the basic fields for event assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "reply_id": reply_id,
        }

    def _get_msg_base(self) -> dict:
        """Get the dict with the basic fields for message assertion."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "metadata": {},
            "name": "Friday",
            "role": "assistant",
        }

    async def test_streaming_reasoning(self) -> None:
        """Test the streaming model inference without tool calls generated,
        only text in model response.

        Test both the reply and replyStream interfaces.
        """
        # Set up mock responses for streaming
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="Hello")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text=" world")],
                        is_last=False,
                    ),
                    ChatResponse(content=[TextBlock(text="!")], is_last=False),
                    ChatResponse(
                        content=[TextBlock(text="Hello world!")],
                        is_last=True,
                    ),
                ],
            ],
        )

        # Test replyStream interface
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {
                "type": "MODEL_CALL_START",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "Hello",
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": " world",
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "!",
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "REPLY_END",
                "session_id": session_id,
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after reply_stream
        msg_base = self._get_msg_base()
        expected_context = [
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi",
            },
            {
                **msg_base,
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        self.assertListEqual(context_dicts, expected_context)

        # Test reply interface
        self.model.cnt = 0  # Reset mock model response index
        msg = await self.agent.reply(UserMsg(name="user", content="Hi again"))
        self.assertDictEqual(
            msg.model_dump(),
            {
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "created_at": AnyString(),
                "id": AnyString(),
                "metadata": {},
            },
        )

        # Assert context after reply
        expected_context_after_reply = [
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi",
                "metadata": {},
            },
            {
                **msg_base,
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "metadata": {},
            },
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi again",
                "metadata": {},
            },
            {
                **msg_base,
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "metadata": {},
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        self.assertListEqual(context_dicts, expected_context_after_reply)

    async def test_non_streaming_reasoning(self) -> None:
        """Test the non-streaming model inference without tool calls generated,
        only text in model response.

        Test both the reply and replyStream interfaces.
        """
        # Set up mock response for non-streaming
        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Hello world!")],
                    is_last=True,
                    usage=None,
                ),
            ],
        )

        # Test replyStream interface
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {
                "type": "MODEL_CALL_START",
                "model_name": "mock-model",
            },
            {
                "type": "TEXT_BLOCK_START",
                "block_id": AnyString(),
            },
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "Hello world!",
            },
            {
                "type": "TEXT_BLOCK_END",
                "block_id": AnyString(),
            },
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "REPLY_END",
                "session_id": session_id,
            },
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after reply_stream
        msg_base = self._get_msg_base()
        expected_context = [
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi",
                "metadata": {},
            },
            {
                **msg_base,
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "metadata": {},
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        self.assertListEqual(context_dicts, expected_context)

        # Test reply interface
        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Hello world!")],
                    is_last=True,
                    usage=None,
                ),
            ],
        )

        msg = await self.agent.reply(UserMsg(name="user", content="Hi again"))
        self.assertDictEqual(
            msg.model_dump(),
            {
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "created_at": AnyString(),
                "id": AnyString(),
                "metadata": {},
            },
        )

        # Assert context after reply
        expected_context_after_reply = [
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi",
                "metadata": {},
            },
            {
                **msg_base,
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "metadata": {},
            },
            {
                **msg_base,
                "name": "user",
                "role": "user",
                "content": "Hi again",
                "metadata": {},
            },
            {
                **msg_base,
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello world!",
                    },
                ],
                "metadata": {},
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        self.assertListEqual(context_dicts, expected_context_after_reply)

    async def test_streaming_sequential_tool_calls(self) -> None:
        """Test the streaming model inference with tool calls generated.

        Test only the replyStream interface. The `is_concurrent_safe` of the
        registered tools should be False to make sure the tool calls are
        executed sequentially.

        To assert:
        1. The events (by assert the dict generated by model_dump)
        2. The final reply message
        3. The agent state (Before and after replyStream)
        """
        # Register sequential tools
        seq_tool = MockSequentialTool()
        self.agent.toolkit.tools[seq_tool.name] = RegisteredTool(
            tool=seq_tool,
            group="basic",
        )

        # Create tool call IDs
        tool_call_id_1 = "tool_call_1"
        tool_call_id_2 = "tool_call_2"

        # Set up mock responses with tool calls
        text_block = TextBlock(text="I'll call the tool")
        tool_call_1 = ToolCallBlock(
            id=tool_call_id_1,
            name="mock_sequential_tool",
            input='{"input": "test1"}',
        )
        tool_call_1_part1 = ToolCallBlock(
            id=tool_call_id_1,
            name="mock_sequential_tool",
            input='{"input": ',
        )
        tool_call_1_part2 = ToolCallBlock(
            id=tool_call_id_1,
            name="mock_sequential_tool",
            input='"test1"}',
        )
        tool_call_2 = ToolCallBlock(
            id=tool_call_id_2,
            name="mock_sequential_tool",
            input='{"input": "test2"}',
        )
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[text_block, tool_call_1_part1],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[tool_call_1_part2],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[tool_call_2],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[text_block, tool_call_1, tool_call_2],
                        is_last=True,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="ended")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="ended")],
                        is_last=True,
                    ),
                ],
            ],
        )

        # Collect all events
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump(mode="json"))

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        # Expected events for sequential tool calls
        expected_events = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {"type": "TEXT_BLOCK_START", "block_id": AnyString()},
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "I'll call the tool",
            },
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_sequential_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": '{"input": ',
            },
            {"type": "TEXT_BLOCK_END", "block_id": AnyString()},
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": '"test1"}',
            },
            {
                "type": "TOOL_CALL_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_sequential_tool",
            },
            {
                "type": "TOOL_CALL_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": '{"input": "test2"}',
            },
            {"type": "TOOL_CALL_END", "tool_call_id": tool_call_id_1},
            {"type": "TOOL_CALL_END", "tool_call_id": tool_call_id_2},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_sequential_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "Sequential result: test1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_sequential_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": "Sequential result: test2",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_2,
                "state": "success",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {"type": "TEXT_BLOCK_START", "block_id": AnyString()},
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "ended",
            },
            {"type": "TEXT_BLOCK_END", "block_id": AnyString()},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {"type": "REPLY_END", "session_id": session_id},
        ]

        basic_dict = self._get_event_base(reply_id)
        self.assertListEqual(
            events,
            [{**basic_dict, **_} for _ in expected_events],
        )

        # Assert context after reply_stream
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "I'll call the tool",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_sequential_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_sequential_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Sequential result: test1",
                            },
                        ],
                        "name": "mock_sequential_tool",
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Sequential result: test2",
                            },
                        ],
                        "name": "mock_sequential_tool",
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "ended",
                    },
                ],
            },
        ]

        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

    async def test_streaming_concurrent_tool_calls(self) -> None:
        """Test the streaming model inference with tool calls generated.

        Test only the replyStream interface. The `is_concurrent_safe` of the
        registered tools should be True to make sure the tool calls are
        executed concurrently.

        To assert:
        1. The events (by assert the dict generated by model_dump)
        2. The final reply message
        3. The agent state (Before and after replyStream)
        """
        # Register concurrent tools
        conc_tool = MockConcurrentTool()

        self.agent.toolkit.tools[conc_tool.name] = RegisteredTool(
            tool=conc_tool,
            group="basic",
        )

        # Create tool call IDs
        tool_call_id_1 = "tool_call_1"
        tool_call_id_2 = "tool_call_2"

        # Set up mock responses with tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_concurrent_tool",
                                input='{"input": "test1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_concurrent_tool",
                                input='{"input": "test2"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="All done")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="All done")],
                        is_last=True,
                    ),
                ],
            ],
        )

        # Collect all events
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        # For concurrent execution, the order of tool results may vary
        # Split events into: prefix (before concurrent), concurrent part,
        # suffix (after concurrent)

        # Expected prefix events (before tool execution)
        expected_prefix = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
        ]

        # Expected concurrent events (order may vary)
        expected_concurrent = [
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_concurrent_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "Concurrent result: test1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_concurrent_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": "Concurrent result: test2",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_2,
                "state": "success",
            },
        ]

        # Expected suffix events (final model call with pure text)
        expected_suffix = [
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {"type": "TEXT_BLOCK_START", "block_id": AnyString()},
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "All done",
            },
            {"type": "TEXT_BLOCK_END", "block_id": AnyString()},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {"type": "REPLY_END", "session_id": session_id},
        ]

        # Assert prefix events (fixed order)
        basic_dict = self._get_event_base(reply_id)
        prefix_len = len(expected_prefix)
        self.assertListEqual(
            events[:prefix_len],
            [{**basic_dict, **_} for _ in expected_prefix],
        )

        # Assert concurrent events (order may vary)
        concurrent_len = len(expected_concurrent)
        concurrent_events = events[prefix_len : prefix_len + concurrent_len]

        # Check length matches
        self.assertEqual(len(concurrent_events), len(expected_concurrent))

        # Check each expected event is in the actual events
        for expected_event in expected_concurrent:
            expected_with_base = {**basic_dict, **expected_event}
            self.assertIn(expected_with_base, concurrent_events)

        # Assert suffix events (fixed order)
        suffix_events = events[prefix_len + concurrent_len :]
        self.assertListEqual(
            suffix_events,
            [{**basic_dict, **_} for _ in expected_suffix],
        )

        # Assert context after reply_stream
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_concurrent_tool",
                        "input": '{"input": "test1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_concurrent_tool",
                        "input": '{"input": "test2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Concurrent result: test1",
                            },
                        ],
                        "name": "mock_concurrent_tool",
                        "state": "success",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Concurrent result: test2",
                            },
                        ],
                        "name": "mock_concurrent_tool",
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "All done",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

    async def test_streaming_mixed_tool_calls(self) -> None:
        """Test the streaming model inference with both sequential and
        concurrent tool calls generated.

        Test only the replyStream interface.

        To assert:
        1. The events (by assert the dict generated by model_dump)
        2. The final reply message
        3. The agent state (Before and after replyStream)
        """
        # Register both sequential and concurrent tools
        seq_tool = MockSequentialTool()
        conc_tool = MockConcurrentTool()

        self.agent.toolkit.tools[seq_tool.name] = RegisteredTool(
            tool=seq_tool,
            group="basic",
        )

        self.agent.toolkit.tools[conc_tool.name] = RegisteredTool(
            tool=conc_tool,
            group="basic",
        )

        # Create tool call IDs
        tool_call_id_1 = "tool_call_1"
        tool_call_id_2 = "tool_call_2"
        tool_call_id_3 = "tool_call_3"

        # Set up mock responses with mixed tool calls
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id_1,
                                name="mock_sequential_tool",
                                input='{"input": "seq1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_2,
                                name="mock_concurrent_tool",
                                input='{"input": "conc1"}',
                            ),
                            ToolCallBlock(
                                id=tool_call_id_3,
                                name="mock_concurrent_tool",
                                input='{"input": "conc2"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="All done")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="All done")],
                        is_last=True,
                    ),
                ],
            ],
        )

        # Collect all events
        events = []
        async for event in self.agent.reply_stream(
            UserMsg(name="user", content="Test"),
        ):
            events.append(event.model_dump())

        # Verify events
        session_id = self.agent.state.session_id
        reply_id = self.agent.state.reply_id

        # For mixed execution: sequential tool first, then concurrent tools
        # Split events into: prefix (before tool execution), sequential part,
        # concurrent part, suffix (final model call)

        # Expected prefix events (before tool execution)
        expected_prefix = [
            {
                "type": "REPLY_START",
                "session_id": session_id,
                "name": "Friday",
                "role": "assistant",
            },
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
        ]

        # Expected sequential tool execution events
        expected_sequential = [
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_1,
                "tool_call_name": "mock_sequential_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_1,
                "delta": "Sequential result: seq1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_1,
                "state": "success",
            },
        ]

        # Expected concurrent events (order may vary)
        expected_concurrent = [
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_2,
                "tool_call_name": "mock_concurrent_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_2,
                "delta": "Concurrent result: conc1",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_2,
                "state": "success",
            },
            {
                "type": "TOOL_RESULT_START",
                "tool_call_id": tool_call_id_3,
                "tool_call_name": "mock_concurrent_tool",
            },
            {
                "type": "TOOL_RESULT_TEXT_DELTA",
                "tool_call_id": tool_call_id_3,
                "delta": "Concurrent result: conc2",
            },
            {
                "type": "TOOL_RESULT_END",
                "tool_call_id": tool_call_id_3,
                "state": "success",
            },
        ]

        # Expected suffix events (final model call with pure text)
        expected_suffix = [
            {"type": "MODEL_CALL_START", "model_name": "mock-model"},
            {"type": "TEXT_BLOCK_START", "block_id": AnyString()},
            {
                "type": "TEXT_BLOCK_DELTA",
                "block_id": AnyString(),
                "delta": "All done",
            },
            {"type": "TEXT_BLOCK_END", "block_id": AnyString()},
            {
                "type": "MODEL_CALL_END",
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {"type": "REPLY_END", "session_id": session_id},
        ]

        # Assert prefix events (fixed order)
        basic_dict = self._get_event_base(reply_id)
        prefix_len = len(expected_prefix)
        self.assertListEqual(
            events[:prefix_len],
            [{**basic_dict, **_} for _ in expected_prefix],
        )

        # Assert sequential events (fixed order)
        sequential_len = len(expected_sequential)
        self.assertListEqual(
            events[prefix_len : prefix_len + sequential_len],
            [{**basic_dict, **_} for _ in expected_sequential],
        )

        # Assert concurrent events (order may vary)
        concurrent_len = len(expected_concurrent)
        concurrent_events = events[
            prefix_len
            + sequential_len : prefix_len
            + sequential_len
            + concurrent_len
        ]

        # Check length matches
        self.assertEqual(len(concurrent_events), len(expected_concurrent))

        # Check each expected event is in the actual events
        for expected_event in expected_concurrent:
            expected_with_base = {**basic_dict, **expected_event}
            self.assertIn(expected_with_base, concurrent_events)

        # Assert suffix events (fixed order)
        suffix_events = events[prefix_len + sequential_len + concurrent_len :]
        self.assertListEqual(
            suffix_events,
            [{**basic_dict, **_} for _ in expected_suffix],
        )

        # Assert context after reply_stream
        msg_base = self._get_msg_base()
        expected_context = [
            {
                "name": "user",
                "role": "user",
                "content": "Test",
            },
            {
                "content": [
                    {
                        "type": "tool_call",
                        "id": tool_call_id_1,
                        "name": "mock_sequential_tool",
                        "input": '{"input": "seq1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_2,
                        "name": "mock_concurrent_tool",
                        "input": '{"input": "conc1"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_call",
                        "id": tool_call_id_3,
                        "name": "mock_concurrent_tool",
                        "input": '{"input": "conc2"}',
                        "state": "finished",
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_sequential_tool",
                        "state": "success",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Sequential result: seq1",
                            },
                        ],
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_concurrent_tool",
                        "state": "success",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Concurrent result: conc1",
                            },
                        ],
                    },
                    {
                        "type": "tool_result",
                        "id": AnyString(),
                        "name": "mock_concurrent_tool",
                        "state": "success",
                        "output": [
                            {
                                "type": "text",
                                "id": AnyString(),
                                "text": "Concurrent result: conc2",
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "All done",
                    },
                ],
            },
        ]
        context_dicts = [msg.model_dump() for msg in self.agent.state.context]
        expected_context = [{**msg_base, **_} for _ in expected_context]
        self.assertListEqual(context_dicts, expected_context)

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
