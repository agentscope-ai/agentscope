# -*- coding: utf-8 -*-
"""Unit tests for the Discord channel agent tools."""

# pylint: disable=protected-access,missing-function-docstring
import json
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest

from agentscope.app.channel import DiscordChannel
from agentscope.message import ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.workspace import WorkspaceBase

# discord.py ships in the optional ``channel`` extra — skip cleanly when
# the environment does not have it installed.
discord = pytest.importorskip("discord")


class _FakePermissions:
    """Stand-in for the permissions object discord.py returns."""

    def __init__(self, view_channel: bool) -> None:
        self.view_channel = view_channel


class _FakeMember:
    """Stand-in for a guild member."""

    def __init__(self, mid: int, name: str, can_view: bool = True) -> None:
        self.id = mid
        self.display_name = name
        self._can_view = can_view


class _FakeRecipient:
    """Stand-in for the discord.py user on the other side of a DM."""

    def __init__(self, uid: int, name: str) -> None:
        self.id = uid
        self.display_name = name
        self.sent: list[tuple[Any, Any]] = []

    async def send(
        self,
        content: str | None = None,
        file: Any = None,
    ) -> None:
        self.sent.append((content, file))


class _FakeTextChannel:
    """Stand-in for a guild text channel (patched in as TextChannel)."""

    def __init__(
        self,
        cid: int,
        name: str,
        guild: "_FakeGuild",
        members: list[_FakeMember],
    ) -> None:
        self.id = cid
        self.name = name
        self.guild = guild
        self._members = members
        self.sent: list[tuple[Any, Any]] = []

    def permissions_for(self, member: _FakeMember) -> _FakePermissions:
        return _FakePermissions(member._can_view)

    async def send(
        self,
        content: str | None = None,
        file: Any = None,
    ) -> None:
        self.sent.append((content, file))


class _FakeOtherChannel:
    """A non-text channel: seen by the bot, never listed."""

    def __init__(self, cid: int, name: str) -> None:
        self.id = cid
        self.name = name


class _FakeDMChannel:
    """Stand-in for a private channel with one other participant."""

    def __init__(self, cid: int, recipient: _FakeRecipient) -> None:
        self.id = cid
        self.recipient = recipient


class _FakeGuild:
    """Stand-in for a guild resolved over REST."""

    def __init__(
        self,
        name: str,
        channels: list[Any],
        members: list[_FakeMember],
    ) -> None:
        self.name = name
        self._channels = channels
        self._members = members
        self.member_scan_limits: list[int] = []
        self.members_error: Exception | None = None

    async def fetch_channels(self) -> list[Any]:
        return list(self._channels)

    async def fetch_members(self, limit: int = 100) -> Any:
        self.member_scan_limits.append(limit)
        if self.members_error is not None:
            raise self.members_error
        for member in self._members:
            yield member


class _FakeClient:
    """REST-capable stand-in for discord.Client."""

    def __init__(
        self,
        guilds: list[_FakeGuild],
        channels: dict[int, Any],
        users: dict[int, _FakeRecipient],
    ) -> None:
        self._guilds = guilds
        self._channels = channels
        self._users = users

    async def fetch_guilds(self) -> Any:
        for guild in self._guilds:
            yield guild

    def get_channel(self, cid: int) -> Any:
        return self._channels.get(cid)

    async def fetch_channel(self, cid: int) -> Any:
        channel = self._channels.get(cid)
        if channel is None:
            raise LookupError(f"unknown channel {cid}")
        return channel

    def get_user(self, uid: int) -> Any:
        return self._users.get(uid)

    async def fetch_user(self, uid: int) -> Any:
        user = self._users.get(uid)
        if user is None:
            raise LookupError(f"unknown user {uid}")
        return user


class _FakeBackend:
    """Workspace backend that records reads and returns fixed files."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.reads: list[str] = []

    async def read_file(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _FakeWorkspace:
    """Workspace wrapper exposing a fake backend."""

    def __init__(self, backend: _FakeBackend) -> None:
        self.backend = backend

    def get_backend(self) -> _FakeBackend:
        return self.backend


def _discord_channel(client: _FakeClient) -> DiscordChannel:
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


def _client() -> (
    tuple[
        _FakeClient,
        _FakeGuild,
        _FakeTextChannel,
        _FakeRecipient,
        _FakeDMChannel,
    ]
):
    """One guild with a text channel, three members, a DM, and a user."""
    members = [
        _FakeMember(222, "Alice"),
        _FakeMember(333, "Bob"),
        _FakeMember(444, "Carol", can_view=False),
    ]
    guild = _FakeGuild("Engineering", [], members)
    general = _FakeTextChannel(111, "general", guild, members)
    guild._channels = [general, _FakeOtherChannel(112, "voice")]
    alice = _FakeRecipient(222, "Alice")
    dm = _FakeDMChannel(555, alice)
    client = _FakeClient(
        [guild],
        {111: general, 555: dm},
        {222: alice},
    )
    return client, guild, general, alice, dm


class DiscordChannelToolTest(IsolatedAsyncioTestCase):
    """Discord discovery, member listing, and target-send tool tests."""

    async def test_list_tools_forms_discovery_send_chain(self) -> None:
        from agentscope.app.channel._discord._tools import (
            ListChatMembers,
            ListChats,
            SendFile,
            SendMessage,
        )

        client, guild, general, alice, _ = _client()
        channel = _discord_channel(client)
        backend = _FakeBackend({"/workspace/report.pdf": b"pdf"})
        workspace = cast(WorkspaceBase, _FakeWorkspace(backend))

        tools = await channel.list_tools(workspace)

        self.assertEqual(
            [tool.name for tool in tools],
            ["ListChats", "ListChatMembers", "SendMessage", "SendFile"],
        )
        with patch.object(discord, "TextChannel", _FakeTextChannel):
            chats = await cast(ListChats, tools[0])()
        self.assertEqual(
            json.loads(chats.content[0].text),
            [
                {
                    "target_id": "111",
                    "target_type": "channel",
                    "name": "Engineering#general",
                },
            ],
        )

        members = await cast(ListChatMembers, tools[1])("111")
        member_items = json.loads(members.content[0].text)
        self.assertEqual(
            member_items,
            [
                {"target_id": "222", "target_type": "user", "name": "Alice"},
                {"target_id": "333", "target_type": "user", "name": "Bob"},
            ],
        )
        self.assertEqual(guild.member_scan_limits, [100])

        message = await cast(SendMessage, tools[2])("222", "user", "hello")
        self.assertIn("Sent message", message.content[0].text)
        self.assertEqual(alice.sent, [("hello", None)])

        file_result = await cast(SendFile, tools[3])(
            "/workspace/report.pdf",
            "111",
            "channel",
        )
        self.assertIn("Sent file", file_result.content[0].text)
        self.assertEqual(backend.reads, ["/workspace/report.pdf"])
        self.assertEqual(general.sent[0][0], None)
        self.assertEqual(general.sent[0][1].filename, "report.pdf")

    async def test_list_chats_filters_by_name_query(self) -> None:
        from agentscope.app.channel._discord._tools import ListChats

        client, _, _, _, _ = _client()
        channel = _discord_channel(client)
        tool = ListChats(
            channel,
            _FakeBackend(),
        )

        with patch.object(discord, "TextChannel", _FakeTextChannel):
            misses = await tool("nowhere")
            hits = await tool("GEN")

        self.assertEqual(json.loads(misses.content[0].text), [])
        self.assertEqual(
            json.loads(hits.content[0].text)[0]["name"],
            "Engineering#general",
        )

    async def test_list_chat_members_dm_returns_recipient(self) -> None:
        from agentscope.app.channel._discord._tools import ListChatMembers

        client, _, _, _, _ = _client()
        channel = _discord_channel(client)
        tool = ListChatMembers(channel, _FakeBackend())

        members = await tool("555")

        self.assertEqual(
            json.loads(members.content[0].text),
            [{"target_id": "222", "target_type": "user", "name": "Alice"}],
        )

    async def test_list_chat_members_missing_intent_is_visible(self) -> None:
        from agentscope.app.channel._discord._tools import ListChatMembers

        client, guild, _, _, _ = _client()
        guild.members_error = RuntimeError(
            "403 Forbidden (error code: 50001): Missing Access",
        )
        channel = _discord_channel(client)
        tool = ListChatMembers(channel, _FakeBackend())

        result = await tool("111")

        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("Missing Access", result.content[0].text)
        self.assertIn("Server Members", result.content[0].text)

    async def test_send_message_splits_at_discord_cap(self) -> None:
        from agentscope.app.channel._discord._tools import SendMessage

        client, _, _, alice, _ = _client()
        channel = _discord_channel(client)
        tool = SendMessage(channel, _FakeBackend())

        result = await tool("222", "user", "x" * 3500)

        self.assertIn("Sent message", result.content[0].text)
        self.assertEqual(
            [len(content) for content, _ in alice.sent],
            [2000, 1500],
        )

    async def test_send_message_error_is_reported(self) -> None:
        from agentscope.app.channel._discord._tools import SendMessage

        client, _, _, _, _ = _client()
        channel = _discord_channel(client)
        tool = SendMessage(channel, _FakeBackend())

        result = await tool("999", "channel", "hello")

        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("Failed to send message", result.content[0].text)
        self.assertIn("999", result.content[0].text)

    async def test_send_file_rejects_oversize_payload(self) -> None:
        from agentscope.app.channel._discord._tools import SendFile

        client, _, general, _, _ = _client()
        channel = _discord_channel(client)
        backend = _FakeBackend(
            {"/workspace/big.bin": b"x" * (8 * 1024**2 + 1)}
        )
        tool = SendFile(channel, backend)

        result = await tool("/workspace/big.bin", "111", "channel")

        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("may upload at most", result.content[0].text)
        self.assertEqual(general.sent, [])

    async def test_send_file_missing_workspace_file_is_error(self) -> None:
        from agentscope.app.channel._discord._tools import SendFile

        client, _, _, _, _ = _client()
        channel = _discord_channel(client)
        tool = SendFile(channel, _FakeBackend())

        result = await tool("/workspace/gone.pdf", "111", "channel")

        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("cannot read", result.content[0].text)

    async def test_discord_tool_permissions_match_platform_policy(
        self,
    ) -> None:
        client, _, _, _, _ = _client()
        channel = _discord_channel(client)
        tools = await channel.list_tools(
            cast(WorkspaceBase, _FakeWorkspace(_FakeBackend())),
        )

        read_decision = await tools[0].check_permissions(
            {},
            PermissionContext(),
        )
        send_decision = await tools[2].check_permissions(
            {},
            PermissionContext(),
        )

        self.assertEqual(read_decision.behavior, PermissionBehavior.ALLOW)
        self.assertEqual(send_decision.behavior, PermissionBehavior.ASK)
