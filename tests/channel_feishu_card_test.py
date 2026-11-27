# -*- coding: utf-8 -*-
"""Regression tests for Feishu streaming-card delivery."""

# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import FeishuChannel
from agentscope.app.channel._base import ChannelEvent
from agentscope.message import TextBlock


def _channel() -> FeishuChannel:
    """A Feishu channel that never logs in."""
    return FeishuChannel(
        "feishu-1",
        FeishuChannel.Credentials(app_id="app-id", app_secret="secret"),
        FeishuChannel.Config(),
    )


def _event() -> ChannelEvent:
    """The inbound message a reply is delivered against."""
    return ChannelEvent(
        channel_id="feishu-1",
        channel_user_id="ou-1",
        chat_id="chat-1",
        channel_message_id="om-1",
        content=[TextBlock(text="hi")],
    )


class FeishuCardFallbackTest(IsolatedAsyncioTestCase):
    """A failed card update must not lose the reply."""

    async def test_finish_card_sends_text_when_the_update_fails(self) -> None:
        """An expired token or a rate limit on the card update used to drop
        the whole answer silently."""
        channel = _channel()

        with (
            patch.object(
                channel,
                "_api",
                new=AsyncMock(return_value={"code": 230001, "msg": "failed"}),
            ),
            patch.object(
                channel,
                "_send",
                new=AsyncMock(return_value={"code": 0}),
            ) as send,
        ):
            await channel._finish_card(_event(), "card-1", "the answer")

        send.assert_awaited_once()
        self.assertEqual(
            send.await_args.args[2:],
            ("text", '{"text": "the answer"}'),
        )

    async def test_finish_card_keeps_the_card_when_the_update_succeeds(
        self,
    ) -> None:
        """A successful update is delivered once, with no duplicate text."""
        channel = _channel()

        with (
            patch.object(
                channel,
                "_api",
                new=AsyncMock(return_value={"code": 0}),
            ),
            patch.object(channel, "_send", new=AsyncMock()) as send,
        ):
            await channel._finish_card(_event(), "card-1", "the answer")

        send.assert_not_awaited()
