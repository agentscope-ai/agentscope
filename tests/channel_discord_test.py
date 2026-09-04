# -*- coding: utf-8 -*-
"""Unit tests for Discord agent-callable tools."""

# pylint: disable=protected-access

import json
import sys
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel import DiscordChannel
from agentscope.message import ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.workspace import WorkspaceBase


class _FakeBackend:
    """Workspace backend that records reads and returns fixed files."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}
        self.reads: list[str] = []

    async def read_file(self, path: str) -> bytes:
        """Return one configured file or raise ``FileNotFoundError``."""
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _FakeWorkspace:
    """Workspace wrapper exposing the fake backend."""

    def __init__(self, backend: _FakeBackend | None = None) -> None:
        self.backend = backend or _FakeBackend()

    def get_backend(self) -> _FakeBackend:
        """Return the fake backend."""
        return self.backend


class _FakeMember:
    """Discord guild member with channel visibility metadata."""

    def __init__(self, user_id: int, name: str, visible: bool = True) -> None:
        self.id = user_id
        self.display_name = name
        self.visible = visible


class _FakeGuild:
    """Discord guild exposing REST channel and member iterators."""

    def __init__(
        self,
        name: str,
        channels: list["_FakeTextChannel"] | None = None,
        members: list[_FakeMember] | None = None,
    ) -> None:
        self.name = name
        self.channels = channels or []
        self.members = members or []

    async def fetch_channels(self) -> list["_FakeTextChannel"]:
        """Return configured guild channels."""
        return self.channels

    async def fetch_members(
        self,
        limit: int | None = 1000,
    ) -> AsyncIterator[_FakeMember]:
        """Yield configured members up to ``limit``."""
        count = 0
        for member in self.members:
            if limit is not None and count >= limit:
                break
            count += 1
            yield member


class _FakeTextChannel:
    """Discord guild text channel that records sends."""

    def __init__(
        self,
        channel_id: int,
        name: str,
        guild: _FakeGuild,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.sent: list[dict[str, Any]] = []

    def permissions_for(self, member: _FakeMember) -> SimpleNamespace:
        """Return whether ``member`` can see this channel."""
        return SimpleNamespace(view_channel=member.visible)

    async def send(
        self,
        content: str | None = None,
        *,
        file: object | None = None,
    ) -> None:
        """Record one outbound Discord send."""
        self.sent.append({"content": content, "file": file})


class _FakeDMChannel:
    """Discord DM channel that records sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        content: str | None = None,
        *,
        file: object | None = None,
    ) -> None:
        """Record one outbound Discord send."""
        self.sent.append({"content": content, "file": file})


class _FakeUser:
    """Discord user that can open one DM channel."""

    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.display_name = name
        self.dm = _FakeDMChannel()

    async def create_dm(self) -> _FakeDMChannel:
        """Return the user's DM channel."""
        return self.dm


class _FakeClient:
    """REST-only Discord client for channel behavior tests."""

    def __init__(
        self,
        guilds: list[_FakeGuild] | None = None,
        channels: dict[int, _FakeTextChannel] | None = None,
        users: dict[int, _FakeUser] | None = None,
    ) -> None:
        self.guilds = guilds or []
        self.channels = channels or {}
        self.cached_channels: dict[int, _FakeTextChannel] = {}
        self.users = users or {}
        self.user = SimpleNamespace(id=999)

    async def fetch_guilds(self) -> AsyncIterator[_FakeGuild]:
        """Yield configured guilds."""
        for guild in self.guilds:
            yield guild

    def get_channel(self, channel_id: int) -> _FakeTextChannel | None:
        """Keep cache empty so production uses the REST path."""
        return self.cached_channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> _FakeTextChannel:
        """Return one configured text channel."""
        return self.channels[channel_id]

    async def fetch_user(self, user_id: int) -> _FakeUser:
        """Return one configured user."""
        return self.users[user_id]


class _FakeFile:
    """Capture the bytes passed to ``discord.File``."""

    def __init__(self, stream: Any, filename: str) -> None:
        self.data = stream.read()
        self.filename = filename


class _FakeIntents:
    """Discord intents used by the lazy REST client test."""

    def __init__(self) -> None:
        self.message_content = False
        self.members = False

    @classmethod
    def default(cls) -> "_FakeIntents":
        """Return default intents with privileged members disabled."""
        return cls()


class _FakeLoginClient:
    """Discord client that records login and close operations."""

    def __init__(self, intents: _FakeIntents) -> None:
        self.intents = intents
        self.logins: list[str] = []
        self.closed = False

    async def login(self, token: str) -> None:
        """Record one REST login."""
        self.logins.append(token)

    async def close(self) -> None:
        """Record client close."""
        self.closed = True


def _discord_module() -> SimpleNamespace:
    """Build the runtime types used by Discord channel methods."""
    return SimpleNamespace(
        TextChannel=_FakeTextChannel,
        DMChannel=_FakeDMChannel,
        File=_FakeFile,
    )


async def _tools_by_name(
    channel: DiscordChannel,
    workspace: _FakeWorkspace,
) -> dict[str, Any]:
    """Resolve Discord tools by name."""
    tools = await channel.list_tools(cast(WorkspaceBase, workspace))
    return {tool.name: tool for tool in tools}


def _channel() -> DiscordChannel:
    """Create a Discord channel without connecting to Discord."""
    return DiscordChannel(
        "discord-1",
        DiscordChannel.Credentials(
            bot_token="token",
            application_id="app-1",
        ),
        DiscordChannel.Config(),
    )


class DiscordToolExposureTest(IsolatedAsyncioTestCase):
    """Discord exposes the issue #2248 tool surface."""

    async def test_list_tools_exposes_discovery_and_send_tools(self) -> None:
        """The channel must expose one focused four-tool chain."""
        tools = await _channel().list_tools(_FakeWorkspace())

        self.assertListEqual(
            [tool.name for tool in tools],
            ["ListChats", "ListChatMembers", "SendMessage", "SendFile"],
        )

    async def test_permissions_allow_reads_and_ask_before_sends(self) -> None:
        """Read tools are allowed while cross-target sends require ASK."""
        tools = await _tools_by_name(_channel(), _FakeWorkspace())

        decisions = [
            await tools[name].check_permissions({}, PermissionContext())
            for name in [
                "ListChats",
                "ListChatMembers",
                "SendMessage",
                "SendFile",
            ]
        ]

        self.assertListEqual(
            [decision.behavior for decision in decisions],
            [
                PermissionBehavior.ALLOW,
                PermissionBehavior.ALLOW,
                PermissionBehavior.ASK,
                PermissionBehavior.ASK,
            ],
        )

    async def test_discovery_tools_return_ready_to_send_targets(self) -> None:
        """Discovery output carries explicit channel and user targets."""
        channel = _channel()
        channel.list_bot_chats = AsyncMock(
            return_value=[
                {"chat_id": "10", "name": "Guild#general"},
                {"chat_id": "20", "name": "Guild#finance"},
            ],
        )
        channel.list_chat_members = AsyncMock(
            return_value=[
                {"user_id": "7", "name": "Alice"},
                {"user_id": "8", "name": "Bob"},
            ],
        )
        tools = await _tools_by_name(channel, _FakeWorkspace())

        chats = await tools["ListChats"]("fin")
        members = await tools["ListChatMembers"]("channel:20")

        self.assertListEqual(
            json.loads(chats.content[0].text),
            [{"target": "channel:20", "name": "Guild#finance"}],
        )
        self.assertListEqual(
            json.loads(members.content[0].text),
            [
                {"target": "user:7", "name": "Alice"},
                {"target": "user:8", "name": "Bob"},
            ],
        )

    async def test_send_message_tool_forwards_explicit_target(self) -> None:
        """SendMessage preserves the discovered target and text."""
        channel = _channel()
        channel.send_message_to = AsyncMock(return_value=True)
        tools = await _tools_by_name(channel, _FakeWorkspace())

        result = await tools["SendMessage"]("user:7", "hello")

        self.assertIn("Sent message", result.content[0].text)
        channel.send_message_to.assert_awaited_once_with("user:7", "hello")


class DiscordChannelToolBehaviorTest(IsolatedAsyncioTestCase):
    """Discord REST helpers support tool discovery and explicit targets."""

    async def test_lazy_rest_client_enables_member_queries_only_locally(
        self,
    ) -> None:
        """The login-only client enables member REST without a gateway."""
        module = SimpleNamespace(
            Intents=_FakeIntents,
            Client=_FakeLoginClient,
        )
        channel = _channel()

        with patch.dict(sys.modules, {"discord": module}):
            client = cast(_FakeLoginClient, await channel._ensure_client())

        self.assertTrue(client.intents.message_content)
        self.assertTrue(client.intents.members)
        self.assertListEqual(client.logins, ["token"])

    async def test_list_chat_members_filters_visibility_and_bot(self) -> None:
        """Only visible humans become direct-message targets."""
        guild = _FakeGuild(
            "Guild",
            members=[
                _FakeMember(7, "Alice"),
                _FakeMember(8, "Hidden", visible=False),
                _FakeMember(999, "Bot"),
            ],
        )
        text_channel = _FakeTextChannel(20, "finance", guild)
        client = _FakeClient(channels={20: text_channel})
        channel = _channel()

        with (
            patch.dict(sys.modules, {"discord": _discord_module()}),
            patch.object(
                channel,
                "_ensure_client",
                AsyncMock(return_value=client),
            ),
        ):
            members = await channel.list_chat_members("20")

        self.assertListEqual(
            members,
            [{"user_id": "7", "name": "Alice"}],
        )

    async def test_send_message_supports_channel_and_user_targets(
        self,
    ) -> None:
        """Messages route to channels or DMs and split at Discord's limit."""
        guild = _FakeGuild("Guild")
        text_channel = _FakeTextChannel(20, "finance", guild)
        user = _FakeUser(7, "Alice")
        client = _FakeClient(channels={20: text_channel}, users={7: user})
        channel = _channel()

        with (
            patch.dict(sys.modules, {"discord": _discord_module()}),
            patch.object(
                channel,
                "_ensure_client",
                AsyncMock(return_value=client),
            ),
        ):
            channel_ok = await channel.send_message_to(
                "channel:20",
                "x" * 2001,
            )
            user_ok = await channel.send_message_to("user:7", "hello")
            invalid = await channel.send_message_to("unknown:7", "ignored")

        self.assertTrue(channel_ok)
        self.assertTrue(user_ok)
        self.assertFalse(invalid)
        self.assertListEqual(
            [item["content"] for item in text_channel.sent],
            ["x" * 2000, "x"],
        )
        self.assertListEqual(
            user.dm.sent,
            [{"content": "hello", "file": None}],
        )

    async def test_send_file_uses_workspace_bytes_and_filename(self) -> None:
        """SendFile reads the session backend and sends one Discord file."""
        guild = _FakeGuild("Guild")
        text_channel = _FakeTextChannel(20, "finance", guild)
        client = _FakeClient(channels={20: text_channel})
        channel = _channel()
        backend = _FakeBackend({"/workspace/report.pdf": b"pdf"})
        tools = await _tools_by_name(channel, _FakeWorkspace(backend))

        with (
            patch.dict(sys.modules, {"discord": _discord_module()}),
            patch.object(
                channel,
                "_ensure_client",
                AsyncMock(return_value=client),
            ),
        ):
            result = await tools["SendFile"](
                "/workspace/report.pdf",
                "channel:20",
            )

        sent_file = cast(_FakeFile, text_channel.sent[0]["file"])
        self.assertIn("Sent file", result.content[0].text)
        self.assertListEqual(backend.reads, ["/workspace/report.pdf"])
        self.assertEqual(sent_file.filename, "report.pdf")
        self.assertEqual(sent_file.data, b"pdf")

    async def test_send_file_reports_workspace_read_failure(self) -> None:
        """A missing workspace file returns an error without a platform
        call."""
        channel = _channel()
        tools = await _tools_by_name(channel, _FakeWorkspace())
        channel.send_file_to = AsyncMock(return_value=True)

        result = await tools["SendFile"](
            "/workspace/missing.pdf",
            "channel:20",
        )

        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("cannot read", result.content[0].text)
        channel.send_file_to.assert_not_awaited()
