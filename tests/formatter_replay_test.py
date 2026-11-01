# -*- coding: utf-8 -*-
"""Replay interrupted sessions through the real Agent and formatter."""
import copy
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import AnthropicChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.session import JSONSession
from agentscope.tool import Toolkit


class ReplayModel(ChatModelBase):
    """Reject invalid text at the model boundary without a provider API."""

    def __init__(self) -> None:
        """Initialize a deterministic model and request recorder."""
        super().__init__("offline-replay", stream=False)
        self.requests: list[list[dict]] = []

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Simulate the nonempty-content constraint of the provider."""
        self.requests.append(copy.deepcopy(messages))
        for message in messages:
            if not message["content"]:
                raise ValueError("empty provider message")
            for block in message["content"]:
                if block["type"] == "text" and block["text"] == "":
                    raise ValueError("empty provider text block")
        return ChatResponse(content=[TextBlock(type="text", text="recovered")])


class FormatterReplayTest(IsolatedAsyncioTestCase):
    """Keep saved history while making replay requests provider-valid."""

    def agent(self, model: ReplayModel) -> ReActAgent:
        """Construct a real Agent without external tools or providers."""
        agent = ReActAgent(
            name="Friday",
            sys_prompt="Continue the saved task.",
            model=model,
            formatter=AnthropicChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=Toolkit(),
        )
        agent.set_console_output_enabled(False)
        return agent

    async def test_saved_interrupted_turn_can_be_replayed_twice(self) -> None:
        """Recover after a JSON round trip without deleting session data."""
        for empty in ("", [], [TextBlock(type="text", text="")]):
            with self.subTest(content=empty):
                with tempfile.TemporaryDirectory() as directory:
                    session = JSONSession(save_dir=directory)
                    first = self.agent(ReplayModel())
                    original = Msg("user", "remember the task", "user")
                    interrupted = Msg("Friday", empty, "assistant")
                    await first.memory.add([original, interrupted])
                    await session.save_session_state("task", agent=first)
                    for _ in range(2):
                        model = ReplayModel()
                        restored = self.agent(model)
                        await session.load_session_state(
                            "task",
                            agent=restored,
                        )
                        saved = await restored.memory.get_memory()
                        self.assertEqual(saved[0].id, original.id)
                        self.assertEqual(saved[1].id, interrupted.id)
                        self.assertEqual(saved[1].content, interrupted.content)
                        reply = await restored(Msg("user", "continue", "user"))
                        self.assertEqual(reply.get_text_content(), "recovered")
                        self.assertEqual(len(model.requests), 1)
                        await session.save_session_state(
                            "task",
                            agent=restored,
                        )

    async def test_empty_text_does_not_remove_tool_protocol(self) -> None:
        """Preserve tool IDs, arguments and empty results beside empty text."""
        messages = [
            Msg("user", "check", "user"),
            Msg(
                "Friday",
                [
                    TextBlock(type="text", text=""),
                    ToolUseBlock(
                        type="tool_use",
                        id="call-1",
                        name="check",
                        input={"value": 2},
                    ),
                ],
                "assistant",
            ),
            Msg(
                "tool",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id="call-1",
                        name="check",
                        output=None,
                    ),
                ],
                "system",
            ),
        ]
        before = copy.deepcopy([m.to_dict() for m in messages])
        formatted = await AnthropicChatFormatter().format(messages)
        self.assertEqual(
            formatted[1]["content"],
            [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "check",
                    "input": {"value": 2},
                },
            ],
        )
        self.assertEqual(formatted[2]["content"][0]["tool_use_id"], "call-1")
        self.assertEqual(formatted[2]["content"][0]["type"], "tool_result")
        self.assertEqual([m.to_dict() for m in messages], before)

    async def test_meaningful_text_is_preserved_exactly(self) -> None:
        """Remove only zero-length blocks without trimming valid text."""
        text = "  partial answer\n"
        formatted = await AnthropicChatFormatter().format(
            [
                Msg("user", "question", "user"),
                Msg(
                    "Friday",
                    [
                        TextBlock(type="text", text=""),
                        TextBlock(type="text", text=text),
                    ],
                    "assistant",
                ),
            ],
        )
        self.assertEqual(
            formatted[-1]["content"],
            [{"type": "text", "text": text}],
        )

    async def test_empty_turn_with_summary_preserves_other_context(
        self,
    ) -> None:
        """A valid summary and trailing user request survive filtering."""
        messages = [
            Msg("system", "keep this summary", "system"),
            Msg("Friday", "", "assistant"),
            Msg("user", "next task", "user"),
        ]
        formatted = await AnthropicChatFormatter().format(messages)
        self.assertEqual([m["role"] for m in formatted], ["system", "user"])
        self.assertEqual(
            formatted[0]["content"][0]["text"],
            "keep this summary",
        )
