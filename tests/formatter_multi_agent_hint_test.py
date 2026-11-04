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
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    HintBlock,
    TextBlock,
    UserMsg,
)


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

    async def test_history_after_hint_keeps_its_tags(self) -> None:
        """Each text segment is history, with the prompt on the first."""
        messages = [
            UserMsg("user", "first"),
            AssistantMsg("Alice", [HintBlock(hint="note")]),
            UserMsg("user", "second"),
        ]
        for cls in (
            AnthropicMultiAgentFormatter,
            MiniMaxMultiAgentFormatter,
            OllamaMultiAgentFormatter,
        ):
            with self.subTest(formatter=cls.__name__):
                formatter = cls(conversation_history_prompt="Custom\n")
                separator = "\n" if cls is OllamaMultiAgentFormatter else " "
                texts = [
                    f"Custom\n<history>\nuser:{separator}first\n</history>",
                    "note",
                    f"<history>\nuser:{separator}second\n</history>",
                ]
                expected = [
                    {
                        "role": "user",
                        "content": (
                            text
                            if cls is OllamaMultiAgentFormatter
                            else [{"type": "text", "text": text}]
                        ),
                    }
                    for text in texts
                ]
                self.assertEqual(await formatter.format(messages), expected)

    async def test_image_before_hint_preserves_history_prompt(self) -> None:
        """An image-only segment leaves the prompt for the first text."""
        source = Base64Source(media_type="image/png", data="YQ==")
        messages = [
            UserMsg("user", [DataBlock(source=source)]),
            AssistantMsg("Alice", [HintBlock(hint="note")]),
            UserMsg("user", "second"),
        ]
        image = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,YQ=="},
        }
        hint = {"type": "text", "text": "note"}
        history = {
            "type": "text",
            "text": "Custom\n<history>\nuser: second\n</history>",
        }
        expected = [
            {"role": "user", "content": [part]}
            for part in (image, hint, history)
        ]
        for cls in (
            OpenAIMultiAgentFormatter,
            DashScopeMultiAgentFormatter,
            MoonshotMultiAgentFormatter,
            VolcengineMultiAgentFormatter,
        ):
            with self.subTest(formatter=cls.__name__):
                formatter = cls(conversation_history_prompt="Custom\n")
                self.assertEqual(await formatter.format(messages), expected)

    async def test_hint_inside_message_preserves_history_order(self) -> None:
        """A standalone hint separates two tagged history messages."""
        mixed_msg = AssistantMsg(
            name="memory",
            content=[
                TextBlock(text="Before"),
                HintBlock(hint="Keep this instruction."),
                TextBlock(text="After"),
            ],
        )
        for cls, _ in FORMATTER_PAIRS:
            with self.subTest(formatter=cls.__name__):
                formatter = cls(conversation_history_prompt="Custom\n")
                separator = "\n" if cls is OllamaMultiAgentFormatter else " "
                texts = [
                    f"Custom\n<history>\nmemory:{separator}Before\n</history>",
                    "Keep this instruction.",
                    f"<history>\nmemory:{separator}After\n</history>",
                ]
                if cls is GeminiMultiAgentFormatter:
                    expected = [
                        {"role": "user", "parts": [{"text": t}]} for t in texts
                    ]
                elif cls in (
                    DeepSeekMultiAgentFormatter,
                    OllamaMultiAgentFormatter,
                ):
                    expected = [{"role": "user", "content": t} for t in texts]
                else:
                    kind = (
                        "input_text"
                        if cls is OpenAIResponseMultiAgentFormatter
                        else "text"
                    )
                    expected = [
                        {
                            "role": "user",
                            "content": [{"type": kind, "text": t}],
                        }
                        for t in texts
                    ]
                self.assertEqual(await formatter.format([mixed_msg]), expected)
