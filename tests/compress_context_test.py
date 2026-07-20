# -*- coding: utf-8 -*-
"""A template test case."""
# pylint: disable=protected-access
import json
import os
import tempfile
from typing import Any

from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel, AnyString

from agentscope.model import StructuredResponse
from agentscope.agent import Agent, ContextConfig
from agentscope.state import AgentState
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    HintBlock,
    Msg,
)
from agentscope.tool import Toolkit


class RecordingStructuredMockModel(MockModel):
    """A mock model that records structured-output compression calls."""

    def __init__(
        self,
        *args: Any,
        fail_structured_output_times: int = 0,
        force_compression_overflow: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the recording mock model."""
        super().__init__(*args, **kwargs)
        self.recorded_structured_messages: list[list[Msg]] = []
        self._fail_structured_output_times = fail_structured_output_times
        self._force_compression_overflow = force_compression_overflow
        self._compression_count_calls = 0

    async def count_tokens(
        self,
        messages: list[Msg],
        tools: list[dict] | None,
    ) -> int:
        """Force the overflow branch when counting compression messages."""
        is_compression_count = bool(
            tools
            and tools[0].get("function", {}).get("name")
            == "generate_structured_output",
        )
        if self._force_compression_overflow and is_compression_count:
            self._compression_count_calls += 1
            if self._compression_count_calls == 1:
                return self.context_size + 1
            return 1
        return await super().count_tokens(messages, tools)

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Any,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Record the structured-output call and optionally fail first."""
        self.recorded_structured_messages.append(list(messages))
        if self._fail_structured_output_times > 0:
            self._fail_structured_output_times -= 1
            raise RuntimeError("simulated compression overflow")
        return await super()._call_api_with_structured_output(
            model_name,
            messages,
            structured_model,
            **kwargs,
        )


def _has_instruction_hint(
    messages: list[Msg],
    instructions: HintBlock,
) -> bool:
    """Return True if messages contain instructions as an assistant hint."""
    for msg in messages:
        if msg.role != "assistant":
            continue
        for hint_block in msg.get_content_blocks("hint"):
            if hint_block.id == instructions.id:
                return hint_block.hint == instructions.hint
    return False


class ContextCompressionTest(IsolatedAsyncioTestCase):
    """The template test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""

    async def test_split_function(self) -> None:
        """The template test."""
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(60 * 4)]),
            model=MockModel(),
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        # When the length of last two messages is exactly appropriate
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )

        self.assertListEqual(
            [_.id for _ in to_compress],
            ["1"],
        )
        self.assertListEqual(
            [_.id for _ in to_reserve],
            ["2", "3"],
        )

        # When one message is in the dividing line
        agent.state.context = [
            UserMsg("User", "".join(["2" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                "".join(["3" for _ in range(15 * 4)]),
                id="2",
            ),
            UserMsg("User", "".join(["3" for _ in range(10 * 4)]), id="3"),
        ]

        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.id for _ in to_compress],
            ["1", "2"],
        )
        self.assertListEqual(
            [_.id for _ in to_reserve],
            ["3"],
        )

        # When compress all messages
        agent.state.context = [
            UserMsg("User", "".join(["2" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                "".join(["3" for _ in range(15 * 4)]),
                id="2",
            ),
            UserMsg("User", "".join(["3" for _ in range(30 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.id for _ in to_compress],
            ["1", "2", "3"],
        )
        self.assertListEqual(
            [_.id for _ in to_reserve],
            [],
        )

        # When the boundary message has multiple blocks
        agent.state.context = [
            UserMsg("User", "".join(["a" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                [
                    TextBlock(
                        text="".join(["b" for _ in range(10 * 4)]),
                        id="b",
                    ),
                    TextBlock(
                        text="".join(["c" for _ in range(10 * 4)]),
                        id="c",
                    ),
                ],
                id="2",
            ),
            UserMsg("User", "".join(["d" for _ in range(10 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_compress],
            [
                {
                    "id": "1",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "a" * 120,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "c" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "d" * 40,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

        # When the boundary message has multiple blocks
        # Cannot leave any blocks
        agent.state.context = [
            UserMsg("User", "".join(["a" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                [
                    TextBlock(
                        text="".join(["b" for _ in range(10 * 4)]),
                        id="b",
                    ),
                    TextBlock(
                        text="".join(["c" for _ in range(15 * 4)]),
                        id="c",
                    ),
                ],
                id="2",
            ),
            UserMsg("User", "".join(["d" for _ in range(10 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_compress],
            [
                {
                    "id": "1",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "a" * 120,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                        },
                        {
                            "id": "c",
                            "text": "c" * 60,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "d" * 40,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

        # Leave the last block of the boundary message
        agent.state.context = [
            UserMsg("User", "".join(["a" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                [
                    TextBlock(
                        text="".join(["b" for _ in range(10 * 4)]),
                        id="b",
                    ),
                    TextBlock(
                        text="".join(["c" for _ in range(5 * 4)]),
                        id="c",
                    ),
                ],
                id="2",
            ),
            UserMsg("User", "".join(["d" for _ in range(10 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_compress],
            [
                {
                    "id": "1",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "a" * 120,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "d" * 40,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

        # Leave all the blocks
        agent.state.context = [
            UserMsg("User", "".join(["a" for _ in range(30 * 4)]), id="1"),
            AssistantMsg(
                "Friday",
                [
                    TextBlock(
                        text="".join(["b" for _ in range(5 * 4)]),
                        id="b",
                    ),
                    TextBlock(
                        text="".join(["c" for _ in range(5 * 4)]),
                        id="c",
                    ),
                ],
                id="2",
            ),
            UserMsg("User", "".join(["d" for _ in range(10 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_compress],
            [
                {
                    "id": "1",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "a" * 120,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 20,
                            "type": "text",
                        },
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "d" * 40,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

        # Leave all the messages
        agent.state.context = [
            AssistantMsg(
                "Friday",
                [
                    TextBlock(
                        text="".join(["b" for _ in range(5 * 4)]),
                        id="b",
                    ),
                    TextBlock(
                        text="".join(["c" for _ in range(5 * 4)]),
                        id="c",
                    ),
                ],
                id="2",
            ),
            UserMsg("User", "".join(["d" for _ in range(10 * 4)]), id="3"),
        ]
        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=80,
            tools=[],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_compress],
            [],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 20,
                            "type": "text",
                        },
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "d" * 40,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

    async def test_split_multi_tool_pairs_reaches_stable_boundary(
        self,
    ) -> None:
        """The boundary is rechecked after moving an unmatched result."""
        agent = Agent(
            name="Friday",
            system_prompt="",
            model=MockModel(context_size=1_000),
            state=AgentState(
                session_id="multi-tool-compression",
                context=[
                    UserMsg("User", "old" * 80, id="old-user"),
                    AssistantMsg(
                        "Friday",
                        [
                            ToolCallBlock(
                                id="tc1",
                                name="first_tool",
                                input=json.dumps({"value": "a" * 80}),
                            ),
                            ToolCallBlock(
                                id="tc2",
                                name="second_tool",
                                input=json.dumps({"value": "b" * 80}),
                            ),
                            ToolResultBlock(
                                id="tc1",
                                name="first_tool",
                                output=[
                                    TextBlock(text="first result " * 8),
                                ],
                                state=ToolResultState.SUCCESS,
                            ),
                            ToolResultBlock(
                                id="tc2",
                                name="second_tool",
                                output=[
                                    TextBlock(text="second result " * 8),
                                ],
                                state=ToolResultState.SUCCESS,
                            ),
                            TextBlock(
                                text="Both tools completed.",
                                id="final-text",
                            ),
                        ],
                        id="multi-tool-msg",
                    ),
                    UserMsg(
                        "User",
                        "latest question",
                        id="latest-user",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        to_compress, to_reserve = await agent._split_context_for_compression(
            to_reserved_tokens=86,
            tools=[],
        )

        self.assertListEqual(
            [msg.model_dump() for msg in to_compress],
            [
                {
                    "id": "old-user",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "old" * 80,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "multi-tool-msg",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "tc1",
                            "type": "tool_call",
                            "name": "first_tool",
                            "input": json.dumps({"value": "a" * 80}),
                            "state": "pending",
                            "suggested_rules": [],
                        },
                        {
                            "id": "tc2",
                            "type": "tool_call",
                            "name": "second_tool",
                            "input": json.dumps({"value": "b" * 80}),
                            "state": "pending",
                            "suggested_rules": [],
                        },
                        {
                            "id": "tc1",
                            "type": "tool_result",
                            "name": "first_tool",
                            "output": [
                                {
                                    "id": AnyString(),
                                    "type": "text",
                                    "text": "first result " * 8,
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                        },
                        {
                            "id": "tc2",
                            "type": "tool_result",
                            "name": "second_tool",
                            "output": [
                                {
                                    "id": AnyString(),
                                    "type": "text",
                                    "text": "second result " * 8,
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )
        self.assertListEqual(
            [msg.model_dump() for msg in to_reserve],
            [
                {
                    "id": "multi-tool-msg",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "final-text",
                            "type": "text",
                            "text": "Both tools completed.",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "latest-user",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "text": "latest question",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

    async def test_context_compression(self) -> None:
        """Test the context compression logic."""
        model = MockModel(context_size=100)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        )

        await agent.compress_context()

        self.assertEqual(
            agent.state.summary,
            """<system-info>Here is a summary of your previous work
# Task Overview
1

# Current State
2

# Important Discoveries
3

# Next Steps
4

# Context to Preserve
5</system-info>""",
        )

        self.assertListEqual(
            [_.model_dump() for _ in agent.state.context],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "2" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "3" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
            ],
        )

    async def test_context_compression_clears_evicted_read_cache(self) -> None:
        """Read cache is cleared when its Read block is compressed out."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "test.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("content\n")

            model = MockModel(context_size=100)
            agent = Agent(
                name="Friday",
                system_prompt="".join(["0" for _ in range(20 * 4)]),
                model=model,
                context_config=ContextConfig(
                    trigger_ratio=0.7,
                    reserve_ratio=0.4,
                ),
                state=AgentState(
                    session_id="123",
                    context=[
                        AssistantMsg(
                            "Friday",
                            [
                                ToolCallBlock(
                                    id="read-call-1",
                                    name="Read",
                                    input=json.dumps(
                                        {"file_path": file_path},
                                    ),
                                ),
                            ],
                            id="1",
                        ),
                        UserMsg(
                            "User",
                            "".join(["2" for _ in range(30 * 4)]),
                            id="2",
                        ),
                        UserMsg(
                            "User",
                            "".join(["3" for _ in range(10 * 4)]),
                            id="3",
                        ),
                    ],
                ),
                toolkit=Toolkit(),
            )
            await agent.state.tool_context.cache_file(
                file_path=file_path,
                lines=["content\n"],
            )
            self.assertIsNotNone(
                await agent.state.tool_context.get_cache(file_path),
            )

            model.set_structured_response(
                StructuredResponse(
                    content={
                        "task_overview": "1",
                        "current_state": "2",
                        "important_discoveries": "3",
                        "next_steps": "4",
                        "context_to_preserve": "5",
                    },
                ),
            )

            await agent.compress_context()

            self.assertIsNone(
                await agent.state.tool_context.get_cache(file_path),
            )

    async def test_context_compression_keeps_reserved_read_cache(
        self,
    ) -> None:
        """Read cache is kept when the same file is still read in context."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "test.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("content\n")

            model = MockModel(context_size=100)
            agent = Agent(
                name="Friday",
                system_prompt="".join(["0" for _ in range(20 * 4)]),
                model=model,
                context_config=ContextConfig(
                    trigger_ratio=0.7,
                    reserve_ratio=0.6,
                ),
                state=AgentState(
                    session_id="123",
                    context=[
                        AssistantMsg(
                            "Friday",
                            [
                                ToolCallBlock(
                                    id="read-call-1",
                                    name="Read",
                                    input=json.dumps(
                                        {"file_path": file_path},
                                    ),
                                ),
                            ],
                            id="1",
                        ),
                        UserMsg(
                            "User",
                            "".join(["2" for _ in range(30 * 4)]),
                            id="2",
                        ),
                        AssistantMsg(
                            "Friday",
                            [
                                ToolCallBlock(
                                    id="read-call-2",
                                    name="Read",
                                    input=json.dumps(
                                        {"file_path": file_path},
                                    ),
                                ),
                            ],
                            id="3",
                        ),
                    ],
                ),
                toolkit=Toolkit(),
            )
            await agent.state.tool_context.cache_file(
                file_path=file_path,
                lines=["content\n"],
            )

            model.set_structured_response(
                StructuredResponse(
                    content={
                        "task_overview": "1",
                        "current_state": "2",
                        "important_discoveries": "3",
                        "next_steps": "4",
                        "context_to_preserve": "5",
                    },
                ),
            )

            await agent.compress_context()

            self.assertIsNotNone(
                await agent.state.tool_context.get_cache(file_path),
            )

    async def test_context_compression_clears_unreferenced_read_cache(
        self,
    ) -> None:
        """Read cache is cleared when no reserved Read references it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "test.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("content\n")

            model = MockModel(context_size=100)
            agent = Agent(
                name="Friday",
                system_prompt="".join(["0" for _ in range(20 * 4)]),
                model=model,
                context_config=ContextConfig(
                    trigger_ratio=0.7,
                    reserve_ratio=0.4,
                ),
                state=AgentState(
                    session_id="123",
                    context=[
                        UserMsg(
                            "User",
                            "".join(["2" for _ in range(60 * 4)]),
                            id="1",
                        ),
                        UserMsg(
                            "User",
                            "".join(["3" for _ in range(30 * 4)]),
                            id="2",
                        ),
                    ],
                ),
                toolkit=Toolkit(),
            )
            await agent.state.tool_context.cache_file(
                file_path=file_path,
                lines=["content\n"],
            )

            model.set_structured_response(
                StructuredResponse(
                    content={
                        "task_overview": "1",
                        "current_state": "2",
                        "important_discoveries": "3",
                        "next_steps": "4",
                        "context_to_preserve": "5",
                    },
                ),
            )

            await agent.compress_context()

            self.assertIsNone(
                await agent.state.tool_context.get_cache(file_path),
            )

    async def test_context_compression_injects_instructions_as_hint(
        self,
    ) -> None:
        """Instructions are injected as a HintBlock only for compression."""
        model = RecordingStructuredMockModel(context_size=100)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        )
        instructions = HintBlock(
            hint="Keep user requirements and file paths.",
            source="user",
        )

        await agent.compress_context(instructions=instructions)

        self.assertEqual(len(model.recorded_structured_messages), 1)
        self.assertTrue(
            _has_instruction_hint(
                model.recorded_structured_messages[0],
                instructions,
            ),
        )
        self.assertFalse(
            any(msg.get_content_blocks("hint") for msg in agent.state.context),
        )

    async def test_context_compression_overflow_retry_keeps_instructions(
        self,
    ) -> None:
        """Overflow retry preserves instructions when rebuilding messages."""
        model = RecordingStructuredMockModel(
            context_size=100,
            fail_structured_output_times=1,
            force_compression_overflow=True,
        )
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.7,
                reserve_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    AssistantMsg(
                        "Friday",
                        "".join(["2" for _ in range(10 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(10 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        model.set_structured_response(
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        )
        instructions = HintBlock(
            hint="Keep the user's original success criteria.",
            source="user",
        )

        await agent.compress_context(instructions=instructions)

        self.assertEqual(len(model.recorded_structured_messages), 2)
        self.assertTrue(
            _has_instruction_hint(
                model.recorded_structured_messages[-1],
                instructions,
            ),
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
