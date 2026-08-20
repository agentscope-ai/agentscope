# -*- coding: utf-8 -*-
"""A template test case."""
# pylint: disable=protected-access
import hashlib
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
    DataBlock,
    Base64Source,
    URLSource,
)
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace


# A 1x1 transparent PNG image
_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB"
    "0C8AAAAASUVORK5CYII="
)


def _image_block(name: str) -> DataBlock:
    """Create a base64 image data block."""
    return DataBlock(
        source=Base64Source(data=_PNG_BASE64, media_type="image/png"),
        name=name,
    )


def _build_image_context() -> list[Msg]:
    """Build a context with 6 images located at the message top level (user
    and assistant), inside a tool result and inside a hint block, plus one
    audio block."""
    return [
        UserMsg(
            "User",
            [_image_block("img1"), TextBlock(type="text", text="hello")],
            id="1",
        ),
        AssistantMsg(
            "Friday",
            [
                ToolCallBlock(
                    type="tool_call",
                    id="call_1",
                    name="view",
                    input="{}",
                ),
                ToolResultBlock(
                    type="tool_result",
                    id="call_1",
                    name="view",
                    output=[
                        TextBlock(type="text", text="the image:"),
                        _image_block("img2"),
                    ],
                    state=ToolResultState.SUCCESS,
                ),
                HintBlock(
                    hint=[
                        TextBlock(type="text", text="a hint image:"),
                        _image_block("img3"),
                    ],
                ),
                _image_block("img4"),
            ],
            id="2",
        ),
        UserMsg(
            "User",
            [
                DataBlock(
                    source=Base64Source(data="AAAA", media_type="audio/wav"),
                ),
                DataBlock(
                    source=URLSource(
                        url="https://example.com/img5.png",
                        media_type="image/png",
                    ),
                    name="img5",
                ),
                _image_block("img6"),
            ],
            id="3",
        ),
    ]


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


class FixedPathOffloader:
    """Return one stable offload path for reminder tests."""

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Return the fixed path without persisting test messages."""
        del session_id, msgs
        return "sessions/123/context.jsonl"


def _make_failing_compression_agent(
    summary: str = "",
    offloader: Any = None,
) -> tuple[Agent, RecordingStructuredMockModel]:
    """Build an agent whose summary generation fails once."""
    model = RecordingStructuredMockModel(
        context_size=100,
        fail_structured_output_times=1,
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
            summary=summary,
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
        offloader=offloader,
        toolkit=Toolkit(),
    )
    return agent, model


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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "c" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "id": "c",
                            "text": "c" * 60,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 20,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "b",
                            "text": "b" * 20,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "id": "c",
                            "text": "c" * 20,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "tc1",
                            "type": "tool_call",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "name": "first_tool",
                            "input": json.dumps({"value": "a" * 80}),
                            "state": "pending",
                            "suggested_rules": [],
                        },
                        {
                            "id": "tc2",
                            "type": "tool_call",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "name": "second_tool",
                            "input": json.dumps({"value": "b" * 80}),
                            "state": "pending",
                            "suggested_rules": [],
                        },
                        {
                            "id": "tc1",
                            "type": "tool_result",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "name": "first_tool",
                            "output": [
                                {
                                    "id": AnyString(),
                                    "type": "text",
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                    "text": "first result " * 8,
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                        },
                        {
                            "id": "tc2",
                            "type": "tool_result",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "name": "second_tool",
                            "output": [
                                {
                                    "id": AnyString(),
                                    "type": "text",
                                    "created_at": AnyString(),
                                    "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": "final-text",
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "Friday",
                    "role": "assistant",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "2" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "usage": None,
                },
                {
                    "id": "3",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                    "name": "User",
                    "role": "user",
                    "content": [
                        {
                            "id": AnyString(),
                            "text": "3" * 40,
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
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

    async def test_max_image_num_without_offloader(self) -> None:
        """The oldest images exceeding the limit are dropped and replaced by
        hints without path information when no offloader is provided."""
        agent = Agent(
            name="Friday",
            system_prompt="You're a helpful assistant.",
            model=MockModel(context_size=100000),
            context_config=ContextConfig(max_image_num=2),
            state=AgentState(session_id="123", context=_build_image_context()),
            toolkit=Toolkit(),
        )

        # The token count is far below the trigger threshold, so only the
        # image limitation takes effect
        await agent.compress_context()

        expected = [
            {
                "name": "User",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<system-reminder>The image named 'img1' is "
                            "removed to free up context "
                            "space.</system-reminder>"
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "text",
                        "text": "hello",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "user",
                "id": "1",
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "name": "Friday",
                "content": [
                    {
                        "type": "tool_call",
                        "id": "call_1",
                        "name": "view",
                        "input": "{}",
                        "state": "pending",
                        "suggested_rules": [],
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "tool_result",
                        "id": "call_1",
                        "name": "view",
                        "output": [
                            {
                                "type": "text",
                                "text": "the image:",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "text",
                                "text": (
                                    "<system-reminder>The image named 'img2' "
                                    "is removed to free up context "
                                    "space.</system-reminder>"
                                ),
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "state": "success",
                        "metadata": {},
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "hint",
                        "hint": [
                            {
                                "type": "text",
                                "text": "a hint image:",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "text",
                                "text": (
                                    "<system-reminder>The image named 'img3' "
                                    "is removed to free up context "
                                    "space.</system-reminder>"
                                ),
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "id": AnyString(),
                        "source": None,
                        "created_at": AnyString(),
                        "finished_at": AnyString(),
                    },
                    {
                        "type": "hint",
                        "hint": (
                            "<system-reminder>The image named 'img4' is "
                            "removed to free up context "
                            "space.</system-reminder>"
                        ),
                        "id": AnyString(),
                        "source": None,
                        "created_at": AnyString(),
                        "finished_at": AnyString(),
                    },
                ],
                "role": "assistant",
                "id": "2",
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "name": "User",
                "content": [
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": "AAAA",
                            "media_type": "audio/wav",
                        },
                        "name": None,
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "url",
                            "url": "https://example.com/img5.png",
                            "media_type": "image/png",
                        },
                        "name": "img5",
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": _PNG_BASE64,
                            "media_type": "image/png",
                        },
                        "name": "img6",
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "user",
                "id": "3",
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
        ]
        self.assertListEqual(
            [_.model_dump() for _ in agent.state.context],
            expected,
        )

        # Calling again should be a no-op
        await agent.compress_context()
        self.assertListEqual(
            [_.model_dump() for _ in agent.state.context],
            expected,
        )

    async def test_max_image_num_with_offloader(self) -> None:
        """The oldest images exceeding the limit are offloaded and replaced
        by hints recording the offloaded path when an offloader is
        provided; URL images keep their original URL."""
        with tempfile.TemporaryDirectory() as workdir:
            agent = Agent(
                name="Friday",
                system_prompt="You're a helpful assistant.",
                model=MockModel(context_size=100000),
                context_config=ContextConfig(max_image_num=1),
                state=AgentState(
                    session_id="123",
                    context=_build_image_context(),
                ),
                toolkit=Toolkit(),
                offloader=LocalWorkspace(workdir=workdir),
            )

            await agent.compress_context()

            # All the base64 images share the same content, thus the same
            # offloaded path
            rel = "data/" + hashlib.sha256(_PNG_BASE64.encode()).hexdigest()
            url = f"workspace:///{rel}.png"
            self.assertTrue(
                os.path.exists(os.path.join(workdir, rel + ".png")),
            )

            self.assertListEqual(
                [_.model_dump() for _ in agent.state.context],
                [
                    {
                        "name": "User",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "<system-reminder>The image named 'img1' "
                                    "is offloaded into "
                                    + url
                                    + ", you can refer to it when "
                                    "needed.</system-reminder>"
                                ),
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "text",
                                "text": "hello",
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "role": "user",
                        "id": "1",
                        "metadata": {},
                        "created_at": AnyString(),
                        "usage": None,
                        "finished_at": AnyString(),
                        "finished_reason": None,
                        "structured_output": None,
                        "error": None,
                    },
                    {
                        "name": "Friday",
                        "content": [
                            {
                                "type": "tool_call",
                                "id": "call_1",
                                "name": "view",
                                "input": "{}",
                                "state": "pending",
                                "suggested_rules": [],
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "tool_result",
                                "id": "call_1",
                                "name": "view",
                                "output": [
                                    {
                                        "type": "text",
                                        "text": "the image:",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                    {
                                        "type": "text",
                                        "text": (
                                            "<system-reminder>The image named "
                                            "'img2' is offloaded into "
                                            + url
                                            + ", you can refer to it when "
                                            "needed.</system-reminder>"
                                        ),
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                ],
                                "state": "success",
                                "metadata": {},
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "hint",
                                "hint": [
                                    {
                                        "type": "text",
                                        "text": "a hint image:",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                    {
                                        "type": "text",
                                        "text": (
                                            "<system-reminder>The image named "
                                            "'img3' is offloaded into "
                                            + url
                                            + ", you can refer to it when "
                                            "needed.</system-reminder>"
                                        ),
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                    },
                                ],
                                "id": AnyString(),
                                "source": None,
                                "created_at": AnyString(),
                                "finished_at": AnyString(),
                            },
                            {
                                "type": "hint",
                                "hint": (
                                    "<system-reminder>The image named 'img4' "
                                    "is offloaded into "
                                    + url
                                    + ", you can refer to it when "
                                    "needed.</system-reminder>"
                                ),
                                "id": AnyString(),
                                "source": None,
                                "created_at": AnyString(),
                                "finished_at": AnyString(),
                            },
                        ],
                        "role": "assistant",
                        "id": "2",
                        "metadata": {},
                        "created_at": AnyString(),
                        "usage": None,
                        "finished_at": None,
                        "finished_reason": None,
                        "structured_output": None,
                        "error": None,
                    },
                    {
                        "name": "User",
                        "content": [
                            {
                                "type": "data",
                                "id": AnyString(),
                                "source": {
                                    "type": "base64",
                                    "data": "AAAA",
                                    "media_type": "audio/wav",
                                },
                                "name": None,
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "text",
                                "text": (
                                    "<system-reminder>The image named 'img5' "
                                    "is offloaded into "
                                    "https://example.com/img5.png, you can "
                                    "refer to it when needed."
                                    "</system-reminder>"
                                ),
                                "id": AnyString(),
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                            {
                                "type": "data",
                                "id": AnyString(),
                                "source": {
                                    "type": "base64",
                                    "data": _PNG_BASE64,
                                    "media_type": "image/png",
                                },
                                "name": "img6",
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        ],
                        "role": "user",
                        "id": "3",
                        "metadata": {},
                        "created_at": AnyString(),
                        "usage": None,
                        "finished_at": AnyString(),
                        "finished_reason": None,
                        "structured_output": None,
                        "error": None,
                    },
                ],
            )

    async def test_max_image_num_default(self) -> None:
        """The default limit is 5, so only the oldest image is removed for a
        context with 6 images."""
        agent = Agent(
            name="Friday",
            system_prompt="You're a helpful assistant.",
            model=MockModel(context_size=100000),
            state=AgentState(session_id="123", context=_build_image_context()),
            toolkit=Toolkit(),
        )
        self.assertEqual(agent.context_config.max_image_num, 5)

        await agent.compress_context()

        self.assertListEqual(
            [_.model_dump() for _ in agent.state.context],
            [
                {
                    "name": "User",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<system-reminder>The image named 'img1' is "
                                "removed to free up context "
                                "space.</system-reminder>"
                            ),
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "text",
                            "text": "hello",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "role": "user",
                    "id": "1",
                    "metadata": {},
                    "created_at": AnyString(),
                    "usage": None,
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                },
                {
                    "name": "Friday",
                    "content": [
                        {
                            "type": "tool_call",
                            "id": "call_1",
                            "name": "view",
                            "input": "{}",
                            "state": "pending",
                            "suggested_rules": [],
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "tool_result",
                            "id": "call_1",
                            "name": "view",
                            "output": [
                                {
                                    "type": "text",
                                    "text": "the image:",
                                    "id": AnyString(),
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                                {
                                    "type": "data",
                                    "id": AnyString(),
                                    "source": {
                                        "type": "base64",
                                        "data": _PNG_BASE64,
                                        "media_type": "image/png",
                                    },
                                    "name": "img2",
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "hint",
                            "hint": [
                                {
                                    "type": "text",
                                    "text": "a hint image:",
                                    "id": AnyString(),
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                                {
                                    "type": "data",
                                    "id": AnyString(),
                                    "source": {
                                        "type": "base64",
                                        "data": _PNG_BASE64,
                                        "media_type": "image/png",
                                    },
                                    "name": "img3",
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                            ],
                            "id": AnyString(),
                            "source": None,
                            "created_at": AnyString(),
                            "finished_at": AnyString(),
                        },
                        {
                            "type": "data",
                            "id": AnyString(),
                            "source": {
                                "type": "base64",
                                "data": _PNG_BASE64,
                                "media_type": "image/png",
                            },
                            "name": "img4",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "role": "assistant",
                    "id": "2",
                    "metadata": {},
                    "created_at": AnyString(),
                    "usage": None,
                    "finished_at": None,
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                },
                {
                    "name": "User",
                    "content": [
                        {
                            "type": "data",
                            "id": AnyString(),
                            "source": {
                                "type": "base64",
                                "data": "AAAA",
                                "media_type": "audio/wav",
                            },
                            "name": None,
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "data",
                            "id": AnyString(),
                            "source": {
                                "type": "url",
                                "url": "https://example.com/img5.png",
                                "media_type": "image/png",
                            },
                            "name": "img5",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "data",
                            "id": AnyString(),
                            "source": {
                                "type": "base64",
                                "data": _PNG_BASE64,
                                "media_type": "image/png",
                            },
                            "name": "img6",
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "role": "user",
                    "id": "3",
                    "metadata": {},
                    "created_at": AnyString(),
                    "usage": None,
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                },
            ],
        )

    async def test_summary_failure_truncates_context(self) -> None:
        """Summary failures fall back to lossy context truncation."""
        agent, _ = _make_failing_compression_agent()
        expected_state = agent.state.model_copy(deep=True)
        expected_state.context = expected_state.context[1:]
        expected_state.summary = (
            "<system-info>Some earlier messages were truncated for limited "
            "context.</system-info>"
        )

        await agent.compress_context()

        self.assertEqual(agent.state, expected_state)

    async def test_offload_reminder_is_not_duplicated(self) -> None:
        """Repeated fallback preserves one reminder for a stable path."""
        reminder = (
            "<system-reminder>The compressed context is offloaded to "
            "'sessions/123/context.jsonl', you can refer to it when "
            "needed.</system-reminder>"
        )
        agent, _ = _make_failing_compression_agent(
            summary=reminder,
            offloader=FixedPathOffloader(),
        )
        expected_state = agent.state.model_copy(deep=True)
        expected_state.context = []

        await agent.compress_context()

        self.assertEqual(agent.state, expected_state)

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
