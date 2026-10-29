# -*- coding: utf-8 -*-
"""Regression tests for HintBlock handling in multi-agent formatters."""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
    MiniMaxChatFormatter,
    MiniMaxMultiAgentFormatter,
    MoonshotChatFormatter,
    MoonshotMultiAgentFormatter,
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
    OpenAIResponseFormatter,
    OpenAIResponseMultiAgentFormatter,
    VolcengineChatFormatter,
    VolcengineMultiAgentFormatter,
)
from agentscope.message import AssistantMsg, HintBlock, TextBlock, UserMsg


FORMATTER_PAIRS = (
    (AnthropicMultiAgentFormatter, AnthropicChatFormatter),
    (DashScopeMultiAgentFormatter, DashScopeChatFormatter),
    (DeepSeekMultiAgentFormatter, DeepSeekChatFormatter),
    (GeminiMultiAgentFormatter, GeminiChatFormatter),
    (MiniMaxMultiAgentFormatter, MiniMaxChatFormatter),
    (MoonshotMultiAgentFormatter, MoonshotChatFormatter),
    (OllamaMultiAgentFormatter, OllamaChatFormatter),
    (OpenAIMultiAgentFormatter, OpenAIChatFormatter),
    (OpenAIResponseMultiAgentFormatter, OpenAIResponseFormatter),
    (VolcengineMultiAgentFormatter, VolcengineChatFormatter),
)


class TestMultiAgentHintFormatting(IsolatedAsyncioTestCase):
    """Check the complete provider payload produced for HintBlock messages."""

    async def test_hint_only_message_matches_chat_formatter(self) -> None:
        """A memory-only hint is emitted instead of disappearing."""
        hint_msg = AssistantMsg(
            name="memory",
            content=[HintBlock(hint="Remember the user's preference.")],
        )
        question_msg = UserMsg(
            name="alice",
            content="What should I remember?",
        )

        for multi_agent_cls, chat_cls in FORMATTER_PAIRS:
            with self.subTest(formatter=multi_agent_cls.__name__):
                multi_agent_formatter = multi_agent_cls()
                chat_formatter = chat_cls()
                actual = await multi_agent_formatter.format(
                    [hint_msg, question_msg],
                )
                expected = await chat_formatter.format([hint_msg])
                expected.extend(
                    await multi_agent_formatter.format([question_msg]),
                )

                self.assertListEqual(expected, actual)

    async def test_hint_inside_message_preserves_history_order(self) -> None:
        """Text on both sides of a hint remains in order."""
        mixed_msg = AssistantMsg(
            name="memory",
            content=[
                TextBlock(text="Context before the hint."),
                HintBlock(hint="Keep this instruction."),
                TextBlock(text="Context after the hint."),
            ],
        )
        before_hint = mixed_msg.model_copy(
            update={"content": [mixed_msg.content[0]]},
        )
        hint_only = mixed_msg.model_copy(
            update={"content": [mixed_msg.content[1]]},
        )
        after_hint = mixed_msg.model_copy(
            update={"content": [mixed_msg.content[2]]},
        )

        for multi_agent_cls, _chat_cls in FORMATTER_PAIRS:
            with self.subTest(formatter=multi_agent_cls.__name__):
                multi_agent_formatter = multi_agent_cls()
                expected = await multi_agent_formatter.format(
                    [before_hint, hint_only, after_hint],
                )

                actual = await multi_agent_formatter.format([mixed_msg])
                self.assertListEqual(expected, actual)
