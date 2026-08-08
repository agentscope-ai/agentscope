# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for Discord channel best-effort helpers."""
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._discord._channel import DiscordChannel


def _channel() -> DiscordChannel:
    """Create a Discord channel without starting a real gateway client."""
    return DiscordChannel(
        channel_id="channel-1",
        credentials=DiscordChannel.Credentials(
            bot_token="dummy-token",
            application_id="app-1",
        ),
        config=DiscordChannel.Config(),
    )


class _FetchFailureClient:
    """Client double that behaves like an unresolved Discord channel lookup."""

    def get_channel(self, _channel_id: int) -> None:
        """Return no cached channel."""
        return None

    async def fetch_channel(self, _channel_id: int) -> None:
        """Simulate a Discord API lookup failure."""
        raise RuntimeError("fetch failed")


class DiscordChannelHelperTest(IsolatedAsyncioTestCase):
    """Discord management helpers should fail open when unavailable."""

    async def test_helpers_return_empty_values_before_client_ready(
        self,
    ) -> None:
        """Cold channels should not crash management or context helpers."""
        channel = _channel()

        self.assertEqual(await channel.list_bot_chats(), [])
        self.assertEqual(await channel.chat_name("123"), "")
        self.assertIsNone(await channel.chat_kind("123"))

    async def test_helpers_return_empty_values_when_lookup_fails(self) -> None:
        """Failed Discord lookups should behave like unresolved chat ids."""
        channel = _channel()
        channel._client = _FetchFailureClient()

        self.assertEqual(await channel.chat_name("123"), "")
        self.assertIsNone(await channel.chat_kind("123"))
