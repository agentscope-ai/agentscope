# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for Discord channel best-effort helpers."""
from collections.abc import AsyncIterator
import sys
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

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


class _ExpectedDiscordError(Exception):
    """Fake discord.py base exception for expected lookup failures."""


class _EmptyAsyncIterator:
    """Async iterator that yields no items."""

    def __aiter__(self) -> "_EmptyAsyncIterator":
        """Return this iterator."""
        return self

    async def __anext__(self) -> Any:
        """Stop immediately."""
        raise StopAsyncIteration


class _FailingAsyncIterator:
    """Async iterator that raises while fetching the first item."""

    def __aiter__(self) -> "_FailingAsyncIterator":
        """Return this iterator."""
        return self

    async def __anext__(self) -> Any:
        """Raise the simulated Discord client failure."""
        raise AssertionError("broken guild cache")


class _ExpectedLookupFailureClient:
    """Client double that behaves like an unresolved Discord channel lookup."""

    def get_channel(self, _channel_id: int) -> None:
        """Return no cached channel."""
        return None

    async def fetch_channel(self, _channel_id: int) -> None:
        """Simulate a Discord API lookup failure."""
        raise _ExpectedDiscordError("lookup failed")


class _EmptyLookupClient:
    """Client double with no cached or remotely fetched channels."""

    def fetch_guilds(self) -> AsyncIterator[Any]:
        """Return no guilds."""
        return _EmptyAsyncIterator()

    def get_channel(self, _channel_id: int) -> None:
        """Return no cached channel."""
        return None

    async def fetch_channel(self, _channel_id: int) -> None:
        """Return no remotely fetched channel."""
        return None


class _UnexpectedLookupFailureClient:
    """Client double that simulates a non-discord.py failure."""

    def get_channel(self, _channel_id: int) -> None:
        """Return no cached channel."""
        return None

    async def fetch_channel(self, _channel_id: int) -> None:
        """Raise an unexpected failure."""
        raise RuntimeError("fetch failed")


class _UnexpectedGuildFailureClient:
    """Client double that fails while fetching guilds."""

    def fetch_guilds(self) -> AsyncIterator[Any]:
        """Raise an unexpected failure."""
        return _FailingAsyncIterator()


def _discord_module() -> SimpleNamespace:
    """Return a minimal fake discord module for exception classification."""
    return SimpleNamespace(
        DMChannel=object,
        Forbidden=_ExpectedDiscordError,
        HTTPException=_ExpectedDiscordError,
        NotFound=_ExpectedDiscordError,
        TextChannel=object,
    )


class DiscordChannelHelperTest(IsolatedAsyncioTestCase):
    """Discord management helpers should fail open when unavailable."""

    async def test_helpers_return_empty_values_with_empty_lazy_client(
        self,
    ) -> None:
        """Empty Discord REST lookups should not crash helper callers."""
        channel = _channel()
        client = _EmptyLookupClient()

        async def ensure_client() -> _EmptyLookupClient:
            """Return the fake lazy REST client."""
            return client

        with patch.dict(sys.modules, {"discord": _discord_module()}):
            channel._ensure_client = ensure_client
            self.assertEqual(await channel.list_bot_chats(), [])
            self.assertEqual(await channel.chat_name("123"), "")
            self.assertIsNone(await channel.chat_kind("123"))

    async def test_helpers_return_empty_values_when_discord_lookup_fails(
        self,
    ) -> None:
        """Expected discord.py lookup failures should fail open."""
        channel = _channel()
        channel._client = _ExpectedLookupFailureClient()

        with patch.dict(sys.modules, {"discord": _discord_module()}):
            self.assertEqual(await channel.chat_name("123"), "")
            self.assertIsNone(await channel.chat_kind("123"))

    async def test_expected_lookup_failures_are_logged_with_traceback(
        self,
    ) -> None:
        """Expected discord.py lookup failures should keep debug traceback."""
        channel = _channel()
        channel._client = _ExpectedLookupFailureClient()

        with (
            patch.dict(sys.modules, {"discord": _discord_module()}),
            patch(
                "agentscope.app.channel._discord._channel.logger.debug",
            ) as debug,
        ):
            self.assertEqual(await channel.chat_name("123"), "")

        debug.assert_called_with(
            "Discord channel lookup failed",
            exc_info=True,
        )

    async def test_unexpected_lookup_failures_are_not_swallowed(self) -> None:
        """Non-discord.py lookup failures should remain visible."""
        channel = _channel()
        channel._client = _UnexpectedLookupFailureClient()

        with self.assertRaises(RuntimeError):
            await channel.chat_name("123")

    async def test_unexpected_guild_failures_are_not_swallowed(self) -> None:
        """Programmer bugs in guild listing should remain visible."""
        channel = _channel()
        channel._client = _UnexpectedGuildFailureClient()

        with (
            patch.dict(sys.modules, {"discord": _discord_module()}),
            self.assertRaises(AssertionError),
        ):
            await channel.list_bot_chats()
