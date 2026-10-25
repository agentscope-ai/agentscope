# -*- coding: utf-8 -*-
"""Regression tests for DiscordChannel chat-kind/name lookup failures."""

# pylint: disable=protected-access,missing-function-docstring
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest

from agentscope.app.channel import DiscordChannel
from agentscope.app.channel._base import ChatKind

# discord.py ships in the optional ``channel`` extra — skip cleanly when
# the environment does not have it installed.
discord = pytest.importorskip("discord")


class _Channel:
    """A resolved guild-channel stand-in."""

    id = 111  # pylint: disable=invalid-name

    def __init__(self, name: str) -> None:
        self.name = name


class _ForbiddenClient:
    """A client whose channel fetch always fails, e.g. a channel the bot
    lost access to between the inbound message and the session setup."""

    def get_channel(self, cid: int) -> None:
        del cid

    async def fetch_channel(self, cid: int) -> Any:
        del cid
        raise RuntimeError(
            "403 Forbidden (error code: 50001): Missing Access",
        )


class _OkClient:
    """A client resolving every id to one channel object."""

    def __init__(self, channel_obj: Any) -> None:
        self._channel_obj = channel_obj

    def get_channel(self, cid: int) -> Any:
        del cid
        return self._channel_obj

    async def fetch_channel(self, cid: int) -> Any:
        return self.get_channel(cid)


def _discord_channel(client: Any) -> DiscordChannel:
    """A DiscordChannel whose client never needs to log in."""
    channel = DiscordChannel(
        "discord-1",
        DiscordChannel.Credentials(
            bot_token="token",
            application_id="app-id",
        ),
        DiscordChannel.Config(),
    )
    channel._client = client
    return channel


class DiscordChatLookupTest(IsolatedAsyncioTestCase):
    """Chat kind/name must degrade to the contract defaults, never raise."""

    async def test_failed_lookup_returns_contract_defaults(self) -> None:
        channel = _discord_channel(_ForbiddenClient())

        self.assertIsNone(await channel.chat_kind("12345"))
        self.assertEqual(await channel.chat_name("12345"), "")

    async def test_malformed_ids_still_return_defaults(self) -> None:
        channel = _discord_channel(_ForbiddenClient())

        self.assertIsNone(await channel.chat_kind("not-a-number"))
        self.assertEqual(await channel.chat_name(""), "")

    async def test_resolved_channel_classifies_and_names(self) -> None:
        channel = _discord_channel(_OkClient(_Channel("general")))

        self.assertIs(await channel.chat_kind("111"), ChatKind.GROUP)
        self.assertEqual(await channel.chat_name("111"), "general")

    async def test_dm_channel_classifies_private(self) -> None:
        class _FakeDM:  # patched in as discord.DMChannel
            name = ""

        channel = _discord_channel(_OkClient(_FakeDM()))

        with patch.object(discord, "DMChannel", _FakeDM):
            kind = await channel.chat_kind("111")

        self.assertIs(kind, ChatKind.PRIVATE)
