# -*- coding: utf-8 -*-
"""A template test case."""
# pylint: disable=protected-access,unused-argument
import json
import os
import tempfile
from typing import Any

from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel, AnyString

from agentscope.model import ChatResponse, StructuredResponse
from agentscope.agent import Agent, ContextConfig
from agentscope.state import AgentState
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
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

    async def test_self_compact_disabled_by_default(self) -> None:
        """Self-compaction should not add rubric calls by default."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            return StructuredResponse(
                content={
                    "decision": "COMPRESS",
                    "reason": "Would compact if called.",
                },
            )

        model.generate_structured_output = mock_structured_output

        await agent.compress_context()

        self.assertEqual(structured_calls, 0)
        self.assertEqual(agent.state.summary, "")
        self.assertEqual(len(agent.state.context), 2)

    async def test_self_compact_skips_below_early_trigger_ratio(self) -> None:
        """Self-compaction should not probe before its early trigger ratio."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            return StructuredResponse(
                content={
                    "decision": "COMPRESS",
                    "reason": "Would compact if called.",
                },
            )

        model.generate_structured_output = mock_structured_output

        await agent.compress_context()

        self.assertEqual(structured_calls, 0)
        self.assertEqual(agent.state.summary, "")
        self.assertEqual(len(agent.state.context), 1)

    async def test_self_compact_can_trigger_early_compression(self) -> None:
        """Enabled self-compaction can compress before the token threshold."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_responses = [
            StructuredResponse(
                content={
                    "decision": "COMPRESS",
                    "reason": "The old trajectory can be summarized.",
                },
            ),
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        ]
        structured_schemas = []
        rubric_prompt = ""

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal rubric_prompt
            structured_schemas.append(structured_model)
            if len(structured_schemas) == 1:
                rubric_prompt = messages[-1].get_text_content()
            return structured_responses.pop(0)

        model.generate_structured_output = mock_structured_output

        await agent.self_compact_context()

        self.assertEqual(len(structured_schemas), 2)
        self.assertEqual(
            structured_schemas[0]["properties"]["decision"]["enum"],
            ["COMPRESS", "CONTINUE"],
        )
        self.assertIn("40.0% of the model context window", rubric_prompt)
        self.assertIn(
            "between 40.0% and 80.0%",
            rubric_prompt,
        )
        self.assertIn(
            "at 80.0%, normal threshold compression will be forced",
            rubric_prompt,
        )
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
        self.assertListEqual(agent.state.context, [])

    async def test_self_compact_continue_skips_early_compression(self) -> None:
        """The rubric can decline compression below the token threshold."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            return StructuredResponse(
                content={
                    "decision": "CONTINUE",
                    "reason": "Recent details are still needed.",
                },
            )

        model.generate_structured_output = mock_structured_output

        await agent.self_compact_context()

        self.assertEqual(structured_calls, 1)
        self.assertEqual(agent.state.summary, "")
        self.assertEqual(len(agent.state.context), 2)

    async def test_self_compact_probe_interval(self) -> None:
        """Self-compaction respects the configured probe interval."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
                self_compact_probe_interval=2,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            return StructuredResponse(
                content={
                    "decision": "COMPRESS",
                    "reason": "Would compact if probed.",
                },
            )

        model.generate_structured_output = mock_structured_output

        await agent.self_compact_context()

        self.assertEqual(structured_calls, 0)
        self.assertEqual(agent.state.summary, "")

    async def test_self_compact_does_not_gate_threshold_compression(
        self,
    ) -> None:
        """The hard trigger ratio should keep the original compression path."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(50 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(50 * 4)]),
                        id="2",
                    ),
                    UserMsg(
                        "User",
                        "".join(["3" for _ in range(50 * 4)]),
                        id="3",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_schemas = []

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            structured_schemas.append(structured_model)
            return StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            )

        model.generate_structured_output = mock_structured_output

        await agent.compress_context()

        self.assertEqual(len(structured_schemas), 1)
        self.assertIn("task_overview", structured_schemas[0]["properties"])
        self.assertNotEqual(agent.state.summary, "")

    async def test_self_compact_rubric_failure_skips_early_compression(
        self,
    ) -> None:
        """Rubric failures should not interrupt below-threshold replies."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            raise RuntimeError("rubric unavailable")

        model.generate_structured_output = mock_structured_output

        await agent.self_compact_context()

        self.assertEqual(agent.state.summary, "")
        self.assertEqual(len(agent.state.context), 2)

    async def test_self_compact_summary_failure_keeps_context(self) -> None:
        """Optional early compression should fail open below the threshold."""
        model = MockModel(context_size=200)
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_trigger_ratio=0.4,
            ),
            state=AgentState(
                session_id="123",
                cur_iter=1,
                context=[
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                        id="1",
                    ),
                    UserMsg(
                        "User",
                        "".join(["2" for _ in range(30 * 4)]),
                        id="2",
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            if structured_calls == 1:
                return StructuredResponse(
                    content={
                        "decision": "COMPRESS",
                        "reason": "The old trajectory can be summarized.",
                    },
                )
            raise RuntimeError("summary unavailable")

        model.generate_structured_output = mock_structured_output

        await agent.self_compact_context()

        # One rubric call, then the existing compression implementation tries
        # the summary call and its overflow fallback.
        self.assertEqual(structured_calls, 3)
        self.assertEqual(agent.state.summary, "")
        self.assertEqual([_.id for _ in agent.state.context], ["1", "2"])

    async def test_threshold_compression_still_runs_before_reasoning(
        self,
    ) -> None:
        """The original threshold compression still runs before reasoning."""
        model = MockModel(context_size=100, stream=False)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        TextBlock(
                            text="done",
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
            ],
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
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
            ),
            state=AgentState(
                session_id="123",
                context=[
                    UserMsg(
                        "User",
                        "".join(["0" for _ in range(30 * 4)]),
                    ),
                    UserMsg(
                        "User",
                        "".join(["1" for _ in range(30 * 4)]),
                    ),
                ],
            ),
            toolkit=Toolkit(),
        )

        final_msg = await agent.reply(
            UserMsg("User", "go"),
        )

        self.assertEqual(final_msg.get_text_content(), "done")
        self.assertNotEqual(agent.state.summary, "")
        self.assertEqual(agent.state.context[-1].get_text_content(), "done")

    async def test_reply_runs_self_compaction_after_final_message(
        self,
    ) -> None:
        """Optional self-compaction runs after the reply chain finishes."""
        model = MockModel(context_size=200, stream=False)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        TextBlock(
                            text="".join(["2" for _ in range(50 * 4)]),
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
            ],
        )
        structured_responses = [
            StructuredResponse(
                content={
                    "decision": "COMPRESS",
                    "reason": "The completed reply can be summarized.",
                },
            ),
            StructuredResponse(
                content={
                    "task_overview": "1",
                    "current_state": "2",
                    "important_discoveries": "3",
                    "next_steps": "4",
                    "context_to_preserve": "5",
                },
            ),
        ]
        structured_schemas = []

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            structured_schemas.append(structured_model)
            return structured_responses.pop(0)

        model.generate_structured_output = mock_structured_output
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_min_iters=0,
            ),
            toolkit=Toolkit(),
        )

        final_msg = await agent.reply(
            UserMsg("User", "".join(["1" for _ in range(30 * 4)])),
        )

        self.assertEqual(final_msg.get_text_content(), "2" * 200)
        self.assertEqual(len(structured_schemas), 2)
        self.assertEqual(
            structured_schemas[0]["properties"]["decision"]["enum"],
            ["COMPRESS", "CONTINUE"],
        )
        self.assertNotEqual(agent.state.summary, "")

    async def test_reply_does_not_run_threshold_compression_after_reply(
        self,
    ) -> None:
        """Reply-end self-compaction must not force threshold compression."""
        model = MockModel(context_size=200, stream=False)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        TextBlock(
                            text="".join(["2" for _ in range(150 * 4)]),
                        ),
                    ],
                    is_last=True,
                    usage=None,
                ),
            ],
        )

        structured_calls = 0

        async def mock_structured_output(
            messages: list[Msg],
            structured_model: Any,
            **kwargs: Any,
        ) -> StructuredResponse:
            nonlocal structured_calls
            structured_calls += 1
            raise AssertionError("threshold compression should wait")

        model.generate_structured_output = mock_structured_output
        agent = Agent(
            name="Friday",
            system_prompt="".join(["0" for _ in range(20 * 4)]),
            model=model,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
                self_compact_enabled=True,
                self_compact_min_iters=0,
            ),
            toolkit=Toolkit(),
        )

        final_msg = await agent.reply(
            UserMsg("User", "".join(["1" for _ in range(30 * 4)])),
        )

        self.assertEqual(final_msg.get_text_content(), "2" * 600)
        self.assertEqual(structured_calls, 0)
        self.assertEqual(agent.state.summary, "")

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
