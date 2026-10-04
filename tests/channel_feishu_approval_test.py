# -*- coding: utf-8 -*-
"""Tests for Feishu's synchronous approval callback response."""
# pylint: disable=protected-access,missing-function-docstring
import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.channel import FeishuChannel
from agentscope.app.channel._base import (
    ChannelAuthConfirmationResultEvent,
    ChannelConfirmationResultEvent,
    ChannelDecisionStatus,
)
from agentscope.app.channel._feishu._card_templates import (
    _build_approval_card,
)


def _channel() -> FeishuChannel:
    return FeishuChannel(
        "feishu-1",
        FeishuChannel.Credentials(app_id="app", app_secret="secret"),
        FeishuChannel.Config(),
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
        authorization_finished = asyncio.Event()

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            received.append(event)
            await asyncio.sleep(0.05)
            authorization_finished.set()
            return ChannelDecisionStatus.AUTHORIZED

        channel._emit = emit
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
