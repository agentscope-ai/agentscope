# -*- coding: utf-8 -*-
"""Unsupported media blocks must stay visible as text placeholders in the
OpenAI Responses formatters.

The tool_result branch of the same formatter already keeps a textual
fallback for unsupported media; the user-message branches drop the None
returned by ``_format_response_data_block`` silently, so a media-only
user message disappears from the formatted history.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    OpenAIResponseFormatter,
)
from agentscope.message import (
    Base64Source,
    DataBlock,
    SystemMsg,
    TextBlock,
    UserMsg,
)

UNSUPPORTED_MEDIA_TYPE = "application/x-unknown-media"


class OpenAIResponseUnsupportedMediaTest(IsolatedAsyncioTestCase):
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
    def _user_texts(formatted) -> list:
        texts = []
        for item in formatted:
            content = item.get("content")
            if item.get("role") != "user" or not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    texts.append(part["text"])
        return texts

    async def test_media_only_message_is_kept(self):
        formatted = await self._format_media_only(OpenAIResponseFormatter())
        self.assertTrue(
            any(UNSUPPORTED_MEDIA_TYPE in t for t in self._user_texts(formatted)),
            formatted,
        )

    async def test_mixed_message_keeps_placeholder(self):
        formatted = await self._format_mixed_message(OpenAIResponseFormatter())
        texts = self._user_texts(formatted)
        self.assertTrue(
            any("What is in this file?" in t for t in texts)
            and any(UNSUPPORTED_MEDIA_TYPE in t for t in texts),
            formatted,
        )
