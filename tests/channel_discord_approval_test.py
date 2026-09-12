# -*- coding: utf-8 -*-
"""Unit tests for Discord tool-approval interaction handling."""

# pylint: disable=protected-access,missing-function-docstring
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from agentscope.app.channel._base import ChannelConfirmationResultEvent
from agentscope.app.channel._discord._approval import (
    _approval_custom_id,
    _parse_approval_custom_id,
)
from agentscope.app.channel._discord._channel import DiscordChannel


def _credentials() -> DiscordChannel.Credentials:
    return DiscordChannel.Credentials(
        bot_token="token",
        application_id="app-1",
    )


def _channel() -> DiscordChannel:
    return DiscordChannel("discord-1", _credentials(), DiscordChannel.Config())


class _FakeInteraction:
    """Minimal interaction stand-in for approval tests."""

    def __init__(
        self,
        *,
        custom_id: str,
        channel_id: int = 42,
        user_id: int = 7,
    ) -> None:
        self.channel_id = channel_id
        self.user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace()
        self.message.edit_calls: list[Any] = []
        self.response = SimpleNamespace()
        self.response.defer_calls: list[Any] = []

        async def defer() -> None:
            self.response.defer_calls.append(True)

        self.response.defer = defer

        async def edit(**kwargs: Any) -> None:
            self.message.edit_calls.append(kwargs)

        self.message.edit = edit


class DiscordApprovalCustomIdTest(IsolatedAsyncioTestCase):
    """Encode/decode tests for approval button custom ids."""

    def test_round_trip_includes_agent_and_session(self) -> None:
        custom_id = _approval_custom_id(
            True,
            "tool-1",
            "agent-1",
            "session-1",
        )
        parsed = _parse_approval_custom_id(custom_id)
        self.assertEqual(
            parsed,
            ("tool-1", True, "agent-1", "session-1"),
        )

    def test_round_trip_for_deny(self) -> None:
        custom_id = _approval_custom_id(
            False,
            "call_deny",
            "agent-9",
            "session-9",
        )
        parsed = _parse_approval_custom_id(custom_id)
        self.assertEqual(
            parsed,
            ("call_deny", False, "agent-9", "session-9"),
        )

    def test_rejects_unknown_custom_id(self) -> None:
        self.assertIsNone(_parse_approval_custom_id("other:button"))
        self.assertIsNone(_parse_approval_custom_id(None))

    def test_custom_id_stays_within_discord_limit_for_uuid_ids(self) -> None:
        agent_id = uuid4().hex
        session_id = uuid4().hex
        tool_call_id = uuid4().hex
        approve_id = _approval_custom_id(
            True,
            tool_call_id,
            agent_id,
            session_id,
        )
        deny_id = _approval_custom_id(
            False,
            tool_call_id,
            agent_id,
            session_id,
        )
        self.assertLessEqual(len(approve_id), 100)
        self.assertLessEqual(len(deny_id), 100)


class DiscordApprovalInteractionTest(IsolatedAsyncioTestCase):
    """Approval interaction routing on the listening client."""

    async def test_on_approval_interaction_emits_resume_event(self) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []

        async def emit(event: ChannelConfirmationResultEvent) -> None:
            received.append(event)

        channel._emit = emit
        custom_id = _approval_custom_id(
            True,
            "tool-1",
            "agent-1",
            "session-1",
        )
        interaction = _FakeInteraction(custom_id=custom_id)

        await channel._on_approval_interaction(interaction, custom_id)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].tool_call_id, "tool-1")
        self.assertEqual(received[0].agent_id, "agent-1")
        self.assertEqual(received[0].session_id, "session-1")
        self.assertTrue(received[0].approved)
        self.assertEqual(received[0].chat_id, "42")
        self.assertEqual(received[0].channel_user_id, "7")
        self.assertEqual(interaction.response.defer_calls, [True])
        self.assertEqual(
            interaction.message.edit_calls,
            [{"content": "✅ Approved", "view": None}],
        )

    async def test_on_approval_interaction_ignores_foreign_buttons(
        self,
    ) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []

        async def emit(event: ChannelConfirmationResultEvent) -> None:
            received.append(event)

        channel._emit = emit
        interaction = _FakeInteraction(custom_id="not-ours")

        await channel._on_approval_interaction(interaction, "not-ours")

        self.assertEqual(received, [])
        self.assertEqual(interaction.response.defer_calls, [])

    def test_build_view_sets_custom_ids_for_listener_dispatch(self) -> None:
        channel = _channel()
        view = channel._build_view("tool-1", "agent-1", "session-1")
        custom_ids = [item.custom_id for item in view.children]
        self.assertEqual(len(custom_ids), 2)
        approve = _parse_approval_custom_id(custom_ids[0])
        deny = _parse_approval_custom_id(custom_ids[1])
        self.assertEqual(approve, ("tool-1", True, "agent-1", "session-1"))
        self.assertEqual(deny, ("tool-1", False, "agent-1", "session-1"))
