# -*- coding: utf-8 -*-
"""A textless system message must reach the multi-agent formatters as a
string, never as ``None``.

``Msg.get_text_content()`` returns ``None`` when the message carries no
TextBlock (an empty content list passes the validator), and the
OpenAI-compatible endpoints reject a non-assistant message whose
``content`` is null with a 400.  The chat formatters cannot hit this
because they drop an empty system turn; the multi-agent formatters emit
it as the first message.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AnthropicMultiAgentFormatter,
    DashScopeMultiAgentFormatter,
    DeepSeekMultiAgentFormatter,
    GeminiMultiAgentFormatter,
    MiniMaxMultiAgentFormatter,
    MoonshotMultiAgentFormatter,
    OllamaMultiAgentFormatter,
    OpenAIMultiAgentFormatter,
    OpenAIResponseMultiAgentFormatter,
    VolcengineMultiAgentFormatter,
)
from agentscope.message import SystemMsg, UserMsg


OPENAI_COMPATIBLE = [
    OpenAIMultiAgentFormatter,
    OpenAIResponseMultiAgentFormatter,
    DashScopeMultiAgentFormatter,
    DeepSeekMultiAgentFormatter,
    MoonshotMultiAgentFormatter,
    VolcengineMultiAgentFormatter,
]

SIBLING_FORMATTERS = [
    AnthropicMultiAgentFormatter,
    MiniMaxMultiAgentFormatter,
    OllamaMultiAgentFormatter,
    GeminiMultiAgentFormatter,
]


def _system_payload(turn: dict) -> object:
    """The text-carrying field of a formatted system turn."""
    if "content" in turn:
        return turn["content"]
    return turn.get("parts")


class MultiAgentSystemMessageTest(IsolatedAsyncioTestCase):
    """An empty SystemMsg stays a string in every multi-agent formatter."""

    async def test_openai_compatible_system_content_is_never_null(
        self,
    ) -> None:
        """A system turn carries a string even with no text block."""
        for formatter_cls in OPENAI_COMPATIBLE:
            with self.subTest(formatter=formatter_cls.__name__):
                formatted = await formatter_cls().format(
                    [SystemMsg("sys", []), UserMsg("u", "hi")],
                )
                turn = formatted[0]
                self.assertEqual(turn["role"], "system")
                self.assertIsInstance(turn["content"], str)

    async def test_sibling_formatters_already_emit_text(self) -> None:
        """The providers with their own system branch keep a text part."""
        for formatter_cls in SIBLING_FORMATTERS:
            with self.subTest(formatter=formatter_cls.__name__):
                formatted = await formatter_cls().format(
                    [SystemMsg("sys", []), UserMsg("u", "hi")],
                )
                payload = _system_payload(formatted[0])
                self.assertIsNotNone(payload)

    async def test_text_system_prompt_is_unchanged(self) -> None:
        """A real system prompt is passed through verbatim."""
        formatted = await OpenAIMultiAgentFormatter().format(
            [SystemMsg("sys", "be brief"), UserMsg("u", "hi")],
        )
        self.assertEqual(
            formatted[0],
            {"role": "system", "content": "be brief"},
        )
