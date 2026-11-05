# -*- coding: utf-8 -*-
"""Regression tests for Feishu inbound text normalisation."""

# pylint: disable=protected-access
import json
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import FeishuChannel

_MENTION_KEY = "@_user_1"


def _channel(only_at_reply: bool) -> FeishuChannel:
    """A Feishu channel that never logs in."""
    return FeishuChannel(
        "feishu-1",
        FeishuChannel.Credentials(app_id="app-id", app_secret="secret"),
        FeishuChannel.Config(only_at_reply=only_at_reply),
    )


def _text_event(text: str) -> Any:
    """A lark ``P2ImMessageReceiveV1``-shaped group text message."""
    message = SimpleNamespace(
        message_type="text",
        content=json.dumps({"text": text}),
        chat_id="chat-1",
        chat_type="group",
        message_id="om-1",
        mentions=[
            SimpleNamespace(
                key=_MENTION_KEY,
                id=SimpleNamespace(open_id="ou-1"),
            ),
        ],
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            message=message,
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="ou-1"),
            ),
        ),
        header=SimpleNamespace(tenant_key="tenant-1"),
    )


class FeishuMentionCleanupTest(IsolatedAsyncioTestCase):
    """The platform's mention placeholders must never reach the agent."""

    async def test_mention_key_is_dropped_when_only_at_reply_is_off(
        self,
    ) -> None:
        """With the gate off the bot replies to any group message, and its
        text still carries the ``@_user_1`` placeholder Feishu inserts."""
        channel = _channel(only_at_reply=False)

        with patch.object(channel, "_api", new=AsyncMock(return_value={})):
            event = await channel._normalize(
                _text_event(f"{_MENTION_KEY} 你好"),
            )

        self.assertIsNotNone(event)
        self.assertEqual(event.content[0].text, "你好")

    async def test_mention_key_is_dropped_when_only_at_reply_is_on(
        self,
    ) -> None:
        """The existing gated path keeps dropping the placeholder."""
        channel = _channel(only_at_reply=True)
        channel._bot_open_id = "ou-1"

        with patch.object(channel, "_api", new=AsyncMock(return_value={})):
            event = await channel._normalize(
                _text_event(f"{_MENTION_KEY} 你好"),
            )

        self.assertIsNotNone(event)
        self.assertEqual(event.content[0].text, "你好")
