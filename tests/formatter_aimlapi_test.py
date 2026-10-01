# -*- coding: utf-8 -*-
"""Unit tests for AIMLAPIChatFormatter and AIMLAPIMultiAgentFormatter.

aimlapi.com speaks the OpenAI Chat Completions wire format verbatim, so both
formatters delegate to the OpenAI implementations. These tests pin the two
things that are actually specific to this provider — the delegation itself
and the narrowed input types — plus a round trip through each ``format`` so a
future divergence in the OpenAI formatters shows up here.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AIMLAPIChatFormatter,
    AIMLAPIMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from agentscope.message import (
    AssistantMsg,
    SystemMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)


class TestAIMLAPIFormatter(IsolatedAsyncioTestCase):
    """Tests for the aimlapi.com formatters."""

    def test_delegates_to_the_openai_formatters(self) -> None:
        """Delegation is the point: aimlapi.com is byte-for-byte
        OpenAI-compatible, so the formatting logic is not re-implemented."""
        self.assertTrue(
            issubclass(AIMLAPIChatFormatter, OpenAIChatFormatter),
        )
        self.assertTrue(
            issubclass(AIMLAPIMultiAgentFormatter, OpenAIMultiAgentFormatter),
        )

    def test_input_types(self) -> None:
        """Audio and PDF support is per-model on aimlapi.com rather than
        provider-wide, so the provider-level default is text and images; the
        model cards widen or narrow it per model."""
        for formatter in [
            AIMLAPIChatFormatter(),
            AIMLAPIMultiAgentFormatter(),
        ]:
            with self.subTest(formatter=type(formatter).__name__):
                self.assertEqual(
                    formatter.input_types,
                    ["text/plain", "image/*"],
                )

    async def test_chat_format(self) -> None:
        """A system / user / assistant exchange formats to OpenAI messages."""
        formatted = await AIMLAPIChatFormatter().format(
            [
                SystemMsg(name="system", content="You are helpful."),
                UserMsg(name="user", content="Hi"),
                AssistantMsg(
                    name="assistant",
                    content=[TextBlock(text="Hello!")],
                ),
            ],
        )

        self.assertEqual(
            [(msg["role"], msg.get("content")) for msg in formatted],
            [
                ("system", [{"type": "text", "text": "You are helpful."}]),
                ("user", [{"type": "text", "text": "Hi"}]),
                ("assistant", [{"type": "text", "text": "Hello!"}]),
            ],
        )

    async def test_chat_format_tool_sequence(self) -> None:
        """Tool calls and their results keep the OpenAI shape."""
        formatted = await AIMLAPIChatFormatter().format(
            [
                UserMsg(name="user", content="Weather in Shanghai?"),
                AssistantMsg(
                    name="assistant",
                    content=[
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='{"city": "Shanghai"}',
                        ),
                        ToolResultBlock(
                            id="call-1",
                            name="get_weather",
                            output=[TextBlock(text="sunny")],
                        ),
                    ],
                ),
            ],
        )

        self.assertEqual(formatted[1]["role"], "assistant")
        self.assertEqual(
            formatted[1]["tool_calls"][0]["function"]["name"],
            "get_weather",
        )
        self.assertEqual(formatted[2]["role"], "tool")
        self.assertEqual(formatted[2]["tool_call_id"], "call-1")

    async def test_multi_agent_format_collapses_the_history(self) -> None:
        """The multi-agent variant folds other speakers into one history
        block, as the OpenAI multi-agent formatter does."""
        formatted = await AIMLAPIMultiAgentFormatter().format(
            [
                SystemMsg(name="system", content="You are helpful."),
                UserMsg(name="alice", content="Hi"),
                AssistantMsg(name="bob", content="Hello"),
            ],
        )

        self.assertEqual(formatted[0]["role"], "system")
        self.assertEqual(len(formatted), 2)
        self.assertIn("alice", str(formatted[1]["content"]))
        self.assertIn("bob", str(formatted[1]["content"]))
