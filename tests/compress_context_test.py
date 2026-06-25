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
    HintBlock,
)
from agentscope.tool import Toolkit


class RecordingMockModel(MockModel):
    """A MockModel that records structured-output calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the recording mock model."""
        super().__init__(*args, **kwargs)
        self.recorded_calls: list[tuple[list[Any], dict[str, Any]]] = []

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Any],
        structured_model: Any,
        **kwargs: Any,
    ) -> Any:
        """Record the call and delegate to the parent mock."""
        self.recorded_calls.append((messages, structured_model))
        return await super()._call_api_with_structured_output(
            model_name,
            messages,
            structured_model,
            **kwargs,
        )


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

    async def test_compress_context_with_instructions(self) -> None:
        """Test that user-defined instructions are injected into the
        compression prompt."""
        model = RecordingMockModel(context_size=100)
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
            hint="Focus on the user's intent, not the assistant's filler.",
            source="system",
        )

        await agent.compress_context(instructions=instructions)

        self.assertEqual(len(model.recorded_calls), 1)
        messages, _ = model.recorded_calls[0]

        # The instruction should appear as a user message right before the
        # compression prompt.
        self.assertTrue(
            any(
                msg.role == "user"
                and msg.content[0].text == instructions.hint
                for msg in messages
            ),
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
