# -*- coding: utf-8 -*-
"""Approvals without platform identities are directed to the Web UI."""
# pylint: disable=protected-access,missing-function-docstring
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import (
    DiscordChannel,
    FeishuChannel,
    DingTalkChannel,
)
from agentscope.app.channel._approval import WEB_UI_APPROVAL_NOTICE
from agentscope.app.channel._base import ChannelEvent
from agentscope.event import RequireUserConfirmEvent
from agentscope.message import ToolCallBlock


class WebUIApprovalNoticeTest(IsolatedAsyncioTestCase):
    """Missing platform identities produce notices instead of buttons."""

    async def test_adapters_send_notice_without_actionable_cards(self) -> None:
        event = ChannelEvent(
            channel_id="channel-1",
            channel_user_id="",
            chat_id="chat-1",
        )
        request = RequireUserConfirmEvent(
            reply_id="reply-1",
            tool_calls=[ToolCallBlock(id="tool-1", name="Bash", input="{}")],
        )
        discord = DiscordChannel(
            "channel-1",
            DiscordChannel.Credentials(
                bot_token="token", application_id="app"
            ),
            DiscordChannel.Config(),
        )
        destination = AsyncMock()
        with patch.object(
            discord, "_channel", AsyncMock(return_value=destination)
        ):
            await discord._present_confirm(event, request)
        destination.send.assert_awaited_once_with(WEB_UI_APPROVAL_NOTICE)

        feishu = FeishuChannel(
            "channel-1",
            FeishuChannel.Credentials(app_id="app", app_secret="secret"),
            FeishuChannel.Config(),
        )
        with patch.object(feishu, "_send", new_callable=AsyncMock) as send:
            await feishu._present_confirm(event, request)
        send.assert_awaited_once_with(
            event.channel_message_id,
            event.chat_id,
            "text",
            json.dumps({"text": WEB_UI_APPROVAL_NOTICE}),
        )

        dingtalk = DingTalkChannel(
            "channel-1",
            DingTalkChannel.Credentials(
                client_id="app", client_secret="secret"
            ),
            DingTalkChannel.Config(),
        )
        api = AsyncMock()
        # A Web UI notice must work even without an approval card template.
        with patch.object(dingtalk, "_api", return_value=api):
            await dingtalk._present_confirm(event, request, "agent")
        api.send_text.assert_awaited_once_with(
            "chat-1", WEB_UI_APPROVAL_NOTICE
        )
        api.create_approval_card.assert_not_awaited()
