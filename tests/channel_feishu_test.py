# -*- coding: utf-8 -*-
"""Tests for Feishu group messages and approval callbacks."""

# pylint: disable=protected-access,missing-function-docstring
import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import FeishuChannel
from agentscope.app.channel._base import (
    ChannelAuthConfirmationResultEvent,
    ChannelConfirmationResultEvent,
    ChannelDecisionStatus,
)
from agentscope.app.channel._feishu._card_templates import (
    _build_approval_card,
)

_BOT_OPEN_ID = "ou-bot"
_BOT_MENTION = ("@_user_1", _BOT_OPEN_ID, "Bot")


def _channel(only_at_reply: bool = True) -> FeishuChannel:
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
                    "@_user_1 hello",
                    [_BOT_MENTION],
                    "hello",
                    only_at_reply=only_at_reply,
                )

    async def test_member_mention_alone_is_preserved(self) -> None:
        """Keep the addressee even when the mention is the entire message."""
        for name, expected in [("Alex", "@Alex"), ("", "@ou-member")]:
            with self.subTest(name=name):
                await self._assert_group_text(
                    "@_user_1",
                    [("@_user_1", "ou-member", name)],
                    expected,
                )

    async def test_numbered_mention_keys_match_in_full(self) -> None:
        """Resolve overlapping keys in a message addressed to the bot."""
        await self._assert_group_text(
            "@_user_1 send the report to @_user_10",
            [_BOT_MENTION, ("@_user_10", "ou-member", "Alex")],
            "send the report to @Alex",
            only_at_reply=True,
        )


def _callback() -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={
                    "type": "tool_guard_approval",
                    "chat_id": "chat-1",
                    "action": "approve",
                    "approval_id": "approval-1",
                },
            ),
            operator=SimpleNamespace(open_id="other-user"),
        ),
    )


def _body(response: Any) -> dict:
    if isinstance(response, dict):
        return response
    return {
        "toast": {
            "type": response.toast.type,
            "content": response.toast.content,
        },
        **({"card": response.card} if response.card is not None else {}),
    }


class FeishuApprovalTest(IsolatedAsyncioTestCase):
    """Feishu authorizes synchronously, then resumes in the background."""

    async def test_unauthorized_callback_returns_toast_without_card(
        self,
    ) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            received.append(event)
            return ChannelDecisionStatus.UNAUTHORIZED

        channel._emit = emit
        loop = asyncio.get_running_loop()

        response = await asyncio.to_thread(
            channel._on_card_action,
            _callback(),
            loop,
        )

        body = _body(response)
        self.assertNotIn("card", body)
        self.assertEqual(body["toast"]["type"], "warning")
        self.assertIn("requester", body["toast"]["content"])
        self.assertDictEqual(
            received[0].model_dump(),
            {
                "channel_id": "feishu-1",
                "chat_id": "chat-1",
                "channel_user_id": "other-user",
                "approved": True,
                "actor": "other-user",
                "approval_id": "approval-1",
            },
        )
        self.assertIsInstance(
            received[0],
            ChannelAuthConfirmationResultEvent,
        )

    async def test_authorized_callback_resumes_in_background(self) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []
        resumed = asyncio.Event()

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            received.append(event)
            if isinstance(event, ChannelAuthConfirmationResultEvent):
                return ChannelDecisionStatus.AUTHORIZED
            resumed.set()
            return ChannelDecisionStatus.ACCEPTED

        channel._emit = emit
        response = await asyncio.to_thread(
            channel._on_card_action,
            _callback(),
            asyncio.get_running_loop(),
        )
        await asyncio.wait_for(resumed.wait(), timeout=1.0)

        self.assertIn("card", _body(response))
        self.assertEqual(len(received), 2)
        self.assertIsInstance(
            received[0],
            ChannelAuthConfirmationResultEvent,
        )
        self.assertNotIsInstance(
            received[1],
            ChannelAuthConfirmationResultEvent,
        )

    async def test_slow_resume_does_not_delay_callback(self) -> None:
        channel = _channel()
        resume_started = asyncio.Event()
        release_resume = asyncio.Event()

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            if isinstance(event, ChannelAuthConfirmationResultEvent):
                return ChannelDecisionStatus.AUTHORIZED
            resume_started.set()
            await release_resume.wait()
            return ChannelDecisionStatus.ACCEPTED

        channel._emit = emit
        response = await asyncio.wait_for(
            asyncio.to_thread(
                channel._on_card_action,
                _callback(),
                asyncio.get_running_loop(),
            ),
            timeout=1.0,
        )

        self.assertIn("card", _body(response))
        await asyncio.wait_for(resume_started.wait(), timeout=1.0)
        release_resume.set()
        await asyncio.sleep(0)

    async def test_authorization_timeout_has_no_mutating_second_emit(
        self,
    ) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []
        release_authorization = asyncio.Event()
        authorization_finished = asyncio.Event()

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            received.append(event)
            await release_authorization.wait()
            authorization_finished.set()
            return ChannelDecisionStatus.AUTHORIZED

        channel._emit = emit
        try:
            with patch(
                "agentscope.app.channel._feishu._channel."
                "_AUTHORIZATION_TIMEOUT_SECS",
                0.01,
            ):
                response = await asyncio.to_thread(
                    channel._on_card_action,
                    _callback(),
                    asyncio.get_running_loop(),
                )
        finally:
            # Keep authorization pending until the callback has timed out.
            release_authorization.set()
        await asyncio.wait_for(authorization_finished.wait(), timeout=1.0)

        body = _body(response)
        self.assertNotIn("card", body)
        self.assertIn("try again", body["toast"]["content"])
        self.assertEqual(len(received), 1)
        self.assertIsInstance(
            received[0],
            ChannelAuthConfirmationResultEvent,
        )

    async def test_card_buttons_round_trip_opaque_id(self) -> None:
        card = json.loads(
            _build_approval_card(
                "chat-1",
                "Bash",
                "{}",
                approval_id="approval-1",
            ),
        )

        buttons = card["elements"][2]["actions"]
        self.assertEqual(
            [button["value"]["approval_id"] for button in buttons],
            ["approval-1", "approval-1"],
        )
