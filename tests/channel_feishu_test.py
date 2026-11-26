# -*- coding: utf-8 -*-
"""Regression tests for Feishu inbound text normalisation."""

# pylint: disable=protected-access
import json
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import FeishuChannel

_BOT_OPEN_ID = "ou-bot"
_BOT_MENTION = ("@_user_1", _BOT_OPEN_ID, "机器人")


def _channel(only_at_reply: bool) -> FeishuChannel:
    """A Feishu channel that never logs in."""
    return FeishuChannel(
        "feishu-1",
        FeishuChannel.Credentials(app_id="app-id", app_secret="secret"),
        FeishuChannel.Config(only_at_reply=only_at_reply),
    )


def _text_event(text: str, mentions: list[tuple[str, str, str]]) -> Any:
    """A lark ``P2ImMessageReceiveV1``-shaped group text message."""
    message = SimpleNamespace(
        message_type="text",
        content=json.dumps({"text": text}),
        chat_id="chat-1",
        chat_type="group",
        message_id="om-1",
        mentions=[
            SimpleNamespace(
                key=key,
                id=SimpleNamespace(open_id=open_id),
                name=name,
            )
            for key, open_id, name in mentions
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
    """Test mentions in inbound group text messages."""

    async def _assert_group_text(
        self,
        text: str,
        mentions: list[tuple[str, str, str]],
        expected: str,
        only_at_reply: bool = False,
    ) -> None:
        channel = _channel(only_at_reply)
        channel._bot_open_id = _BOT_OPEN_ID
        with patch.object(channel, "_api", AsyncMock(return_value={})):
            event = await channel._normalize(_text_event(text, mentions))

        self.assertIsNotNone(event)
        self.assertEqual(
            [(block.type, block.text) for block in event.content],
            [("text", expected)],
        )

    async def test_group_text_strips_bot_mention(self) -> None:
        """Strip the bot mention with either reply setting."""
        for only_at_reply in (False, True):
            with self.subTest(only_at_reply=only_at_reply):
                await self._assert_group_text(
                    "@_user_1 你好",
                    [_BOT_MENTION],
                    "你好",
                    only_at_reply=only_at_reply,
                )

    async def test_member_mention_alone_is_preserved(self) -> None:
        """Keep the addressee even when the mention is the entire message."""
        for name, expected in [("张三", "@张三"), ("", "@ou-member")]:
            with self.subTest(name=name):
                await self._assert_group_text(
                    "@_user_1",
                    [("@_user_1", "ou-member", name)],
                    expected,
                )

    async def test_numbered_mention_keys_match_in_full(self) -> None:
        """Resolve overlapping keys in a message addressed to the bot."""
        await self._assert_group_text(
            "@_user_1 请把报告发给 @_user_10",
            [_BOT_MENTION, ("@_user_10", "ou-member", "张三")],
            "请把报告发给 @张三",
            only_at_reply=True,
        )
