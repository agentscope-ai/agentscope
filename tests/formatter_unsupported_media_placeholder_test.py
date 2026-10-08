# -*- coding: utf-8 -*-
"""Unsupported media blocks must stay visible as text placeholders.

The #3105-#3112 series fixed the Anthropic, Ollama, DeepSeek and OpenAI
formatters where an unsupported DataBlock was dropped silently. These
tests cover the Gemini, Volcengine and DashScope formatters, which share
the same drop-silently behaviour in their plain-message branches.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    DashScopeChatFormatter,
    GeminiChatFormatter,
    VolcengineChatFormatter,
)
from agentscope.message import (
    Base64Source,
    DataBlock,
    SystemMsg,
    TextBlock,
    UserMsg,
)

UNSUPPORTED_MEDIA_TYPE = "application/x-unknown-media"


class UnsupportedMediaPlaceholderTest(IsolatedAsyncioTestCase):
    """Formatter keeps unsupported media visible as a text placeholder."""

    async def _format_media_only(self, formatter):
        msgs = [
            SystemMsg(name="system", content="You're a helpful assistant."),
            UserMsg(
                name="user",
                content=[
                    DataBlock(
                        source=Base64Source(
                            data="aGVsbG8=",
                            media_type=UNSUPPORTED_MEDIA_TYPE,
                        ),
                    ),
                ],
            ),
        ]
        return await formatter.format(msgs)

    async def _format_mixed_message(self, formatter):
        msgs = [
            SystemMsg(name="system", content="You're a helpful assistant."),
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="What is in this file?"),
                    DataBlock(
                        source=Base64Source(
                            data="aGVsbG8=",
                            media_type=UNSUPPORTED_MEDIA_TYPE,
                        ),
                    ),
                ],
            ),
        ]
        return await formatter.format(msgs)

    @staticmethod
    def _joined_text(message: dict) -> str:
        parts = message.get("parts") or message.get("content") or []
        texts = []
        for part in parts:
            if isinstance(part, dict) and "text" in part:
                texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        return " ".join(texts)

    @staticmethod
    def _user_texts(formatted) -> list:
        # Gemini has no system role: the SystemMsg becomes the first user
        # message, so match on content rather than on message count.
        return [
            UnsupportedMediaPlaceholderTest._joined_text(m)
            for m in formatted
            if m.get("role") == "user"
        ]

    async def test_gemini_media_only_message_is_kept(self):
        formatted = await self._format_media_only(GeminiChatFormatter())
        self.assertTrue(
            any(
                UNSUPPORTED_MEDIA_TYPE in text
                for text in self._user_texts(formatted)
            ),
            formatted,
        )

    async def test_gemini_mixed_message_keeps_placeholder(self):
        formatted = await self._format_mixed_message(GeminiChatFormatter())
        texts = self._user_texts(formatted)
        self.assertTrue(
            any(
                "What is in this file?" in text
                and UNSUPPORTED_MEDIA_TYPE in text
                for text in texts
            ),
            formatted,
        )

    async def test_volcengine_media_only_message_is_kept(self):
        formatted = await self._format_media_only(VolcengineChatFormatter())
        user_messages = [m for m in formatted if m.get("role") == "user"]
        self.assertEqual(len(user_messages), 1)
        text = self._joined_text(user_messages[0])
        self.assertIn(UNSUPPORTED_MEDIA_TYPE, text)

    async def test_volcengine_mixed_message_keeps_placeholder(self):
        formatted = await self._format_mixed_message(VolcengineChatFormatter())
        user_messages = [m for m in formatted if m.get("role") == "user"]
        self.assertEqual(len(user_messages), 1)
        text = self._joined_text(user_messages[0])
        self.assertIn("What is in this file?", text)
        self.assertIn(UNSUPPORTED_MEDIA_TYPE, text)

    async def test_dashscope_media_only_message_is_kept(self):
        formatted = await self._format_media_only(DashScopeChatFormatter())
        user_messages = [m for m in formatted if m.get("role") == "user"]
        self.assertEqual(len(user_messages), 1)
        text = self._joined_text(user_messages[0])
        self.assertIn(UNSUPPORTED_MEDIA_TYPE, text)

    async def test_dashscope_mixed_message_keeps_placeholder(self):
        formatted = await self._format_mixed_message(DashScopeChatFormatter())
        user_messages = [m for m in formatted if m.get("role") == "user"]
        self.assertEqual(len(user_messages), 1)
        text = self._joined_text(user_messages[0])
        self.assertIn("What is in this file?", text)
        self.assertIn(UNSUPPORTED_MEDIA_TYPE, text)
