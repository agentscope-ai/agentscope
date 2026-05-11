# -*- coding: utf-8 -*-
"""A template test case."""
# pylint: disable=protected-access
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel, AnyString

from agentscope.model import StructuredResponse
from agentscope.agent import Agent, CompressionConfig
from agentscope.state import AgentState
from agentscope.message import UserMsg, AssistantMsg, TextBlock
from agentscope.tool import Toolkit


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
            compression=CompressionConfig(
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
                    "name": "User",
                    "role": "user",
                    "content": "a" * 120,
                    "metadata": {},
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
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
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "c",
                            "text": "c" * 40,
                            "type": "text",
                        },
                    ],
                    "metadata": {},
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "d" * 40,
                    "metadata": {},
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
                    "name": "User",
                    "role": "user",
                    "content": "a" * 120,
                    "metadata": {},
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
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
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "d" * 40,
                    "metadata": {},
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
                    "name": "User",
                    "role": "user",
                    "content": "a" * 120,
                    "metadata": {},
                },
                {
                    "id": "2",
                    "created_at": AnyString(),
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
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
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
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "d" * 40,
                    "metadata": {},
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
                    "name": "User",
                    "role": "user",
                    "content": "a" * 120,
                    "metadata": {},
                },
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in to_reserve],
            [
                {
                    "id": "2",
                    "created_at": AnyString(),
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
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "d" * 40,
                    "metadata": {},
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
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "d" * 40,
                    "metadata": {},
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
            compression=CompressionConfig(
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
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "name": "User",
                    "role": "user",
                    "content": "3" * 40,
                    "metadata": {},
                },
            ],
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
