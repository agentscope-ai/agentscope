# -*- coding: utf-8 -*-
"""Tests for Feishu's synchronous approval callback response."""
# pylint: disable=protected-access,missing-function-docstring
import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel import FeishuChannel
from agentscope.app.channel._base import (
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
                    "tool_call_id": "tool-1",
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
    """Feishu settles a card only after the gateway accepts its click."""

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
        self.assertEqual(received[0].actor, "other-user")
        self.assertEqual(received[0].approval_id, "approval-1")

    async def test_card_buttons_round_trip_opaque_id(self) -> None:
        card = json.loads(
            _build_approval_card(
                "tool-1",
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
