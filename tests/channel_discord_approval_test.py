# -*- coding: utf-8 -*-
"""Tests for Discord's listener-owned approval interactions."""
# pylint: disable=protected-access,missing-function-docstring
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel import DiscordChannel
from agentscope.app.channel._base import (
    ChannelConfirmationResultEvent,
    ChannelDecisionStatus,
)
from agentscope.app.channel._discord._approval import (
    _approval_custom_id,
    _parse_approval_custom_id,
)


class _Response:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self) -> None:
        self.deferred = True


class _Followup:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []

    async def send(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append((content, ephemeral))


class _Message:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)


def _channel() -> DiscordChannel:
    return DiscordChannel(
        "discord-1",
        DiscordChannel.Credentials(
            bot_token="token",
            application_id="application-1",
        ),
        DiscordChannel.Config(),
    )


def _interaction() -> SimpleNamespace:
    return SimpleNamespace(
        channel_id=123,
        user=SimpleNamespace(id=456),
        response=_Response(),
        followup=_Followup(),
        message=_Message(),
    )


class DiscordApprovalTest(IsolatedAsyncioTestCase):
    """Discord routes persistent component ids through the listener."""

    async def test_listener_parses_opaque_component_and_emits_actor(
        self,
    ) -> None:
        channel = _channel()
        received: list[ChannelConfirmationResultEvent] = []

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            received.append(event)
            return ChannelDecisionStatus.ACCEPTED

        channel._emit = emit
        interaction = _interaction()

        await channel._on_approval_interaction(
            interaction,
            _approval_custom_id("opaque-1", False),
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].approval_id, "opaque-1")
        self.assertEqual(received[0].actor, "456")
        self.assertFalse(received[0].approved)
        self.assertTrue(interaction.response.deferred)
        self.assertEqual(interaction.message.edits[0]["content"], "🚫 Denied")

    async def test_unauthorized_click_is_ephemeral_and_keeps_card(
        self,
    ) -> None:
        channel = _channel()

        async def emit(
            event: ChannelConfirmationResultEvent,
        ) -> ChannelDecisionStatus:
            del event
            return ChannelDecisionStatus.UNAUTHORIZED

        channel._emit = emit
        interaction = _interaction()

        await channel._on_approval_interaction(
            interaction,
            _approval_custom_id("opaque-1", True),
        )

        self.assertTrue(interaction.response.deferred)
        self.assertEqual(interaction.message.edits, [])
        self.assertTrue(interaction.followup.messages[0][1])
        self.assertIn("requester", interaction.followup.messages[0][0])

    async def test_component_id_round_trip_and_foreign_id(self) -> None:
        custom_id = _approval_custom_id("a" * 32, True)

        self.assertLessEqual(len(custom_id), 100)
        self.assertEqual(
            _parse_approval_custom_id(custom_id),
            ("a" * 32, True),
        )
        self.assertIsNone(_parse_approval_custom_id("another:button"))
