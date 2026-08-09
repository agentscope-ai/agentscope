# -*- coding: utf-8 -*-
"""Unit tests for the Discord channel agent tools.

The tools are thin wrappers over a handful of channel methods, so they
are exercised here against fakes — no live discord.py client is needed.
"""
# pylint: disable=protected-access,missing-function-docstring
# pylint: disable=unused-argument
import json
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._discord._channel import DiscordChannel
from agentscope.app.channel._discord._tools import (
    ListChatMembers,
    ListChats,
    SendFile,
    SendMessage,
)
from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
)
from agentscope.tool import ToolChunk


class _FakeBackend:
    """A workspace backend stub: serves file bytes from a dict."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = files or {}
        self.read_calls: list[str] = []

    async def read_file(self, path: str) -> bytes:
        self.read_calls.append(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]


class _FakeChannel:
    """A Discord channel stub recording the tool-driven operations."""

    def __init__(self) -> None:
        self.chats: list[dict] = []
        self.members: dict[str, list[dict]] = {}
        self.sent_messages: list[tuple] = []
        self.sent_files: list[tuple] = []
        # failures: map target_id -> error message to inject.
        self.message_failures: dict[str, str] = {}
        self.file_failures: dict[str, str] = {}

    async def list_bot_chats(self) -> list[dict]:
        return self.chats

    async def list_chat_members(self, chat_id: str) -> list[dict]:
        return self.members.get(chat_id, [])

    async def send_message_to(
        self,
        target_id: str,
        target: str,
        text: str,
    ) -> tuple[bool, str]:
        self.sent_messages.append((target_id, target, text))
        if target_id in self.message_failures:
            return False, self.message_failures[target_id]
        return True, ""

    async def send_file_to(
        self,
        target_id: str,
        target: str,
        data: bytes,
        file_name: str,
    ) -> tuple[bool, str]:
        self.sent_files.append((target_id, target, data, file_name))
        if target_id in self.file_failures:
            return False, self.file_failures[target_id]
        return True, ""


class _FakeWorkspace:
    """A workspace stub exposing only ``get_backend``."""

    def __init__(self, backend: _FakeBackend) -> None:
        self._backend = backend

    def get_backend(self) -> _FakeBackend:
        return self._backend


def _text(chunk: ToolChunk) -> str:
    return "".join(b.text for b in chunk.content if isinstance(b, TextBlock))


class ListChatsTest(IsolatedAsyncioTestCase):
    """``ListChats`` serializes the bot's channels as JSON."""

    async def test_lists_all_chats_as_json(self) -> None:
        channel = _FakeChannel()
        channel.chats = [
            {"chat_id": "1", "name": "guild#general"},
            {"chat_id": "2", "name": "guild#random"},
        ]
        tool = ListChats(channel, _FakeBackend())
        chunk = await tool()
        self.assertEqual(
            json.loads(_text(chunk)),
            [
                {"chat_id": "1", "name": "guild#general"},
                {"chat_id": "2", "name": "guild#random"},
            ],
        )
        self.assertNotEqual(chunk.state, ToolResultState.ERROR)

    async def test_query_filters_by_name(self) -> None:
        channel = _FakeChannel()
        channel.chats = [
            {"chat_id": "1", "name": "guild#general"},
            {"chat_id": "2", "name": "guild#random"},
        ]
        tool = ListChats(channel, _FakeBackend())
        chunk = await tool(query="random")
        self.assertEqual(
            json.loads(_text(chunk)),
            [{"chat_id": "2", "name": "guild#random"}],
        )


class ListChatMembersTest(IsolatedAsyncioTestCase):
    """``ListChatMembers`` serializes a channel's members as JSON."""

    async def test_lists_members_as_json(self) -> None:
        channel = _FakeChannel()
        channel.members = {
            "1": [
                {"user_id": "10", "name": "Alice"},
                {"user_id": "11", "name": "Bob"},
            ],
        }
        tool = ListChatMembers(channel, _FakeBackend())
        chunk = await tool(chat_id="1")
        self.assertEqual(
            json.loads(_text(chunk)),
            [
                {"user_id": "10", "name": "Alice"},
                {"user_id": "11", "name": "Bob"},
            ],
        )


class SendMessageTest(IsolatedAsyncioTestCase):
    """``SendMessage`` reports success/failure from the channel."""

    async def test_success_acks(self) -> None:
        channel = _FakeChannel()
        tool = SendMessage(channel, _FakeBackend())
        chunk = await tool(
            target_id="1",
            target="channel",
            text="hello",
        )
        self.assertNotEqual(chunk.state, ToolResultState.ERROR)
        self.assertIn("Sent message to 1", _text(chunk))
        self.assertEqual(
            channel.sent_messages,
            [("1", "channel", "hello")],
        )

    async def test_failure_returns_error_chunk(self) -> None:
        channel = _FakeChannel()
        channel.message_failures["1"] = "boom"
        tool = SendMessage(channel, _FakeBackend())
        chunk = await tool(
            target_id="1",
            target="channel",
            text="hello",
        )
        self.assertEqual(chunk.state, ToolResultState.ERROR)
        self.assertIn("boom", _text(chunk))


class SendFileTest(IsolatedAsyncioTestCase):
    """``SendFile`` reads from the workspace backend then sends."""

    async def test_reads_workspace_file_and_sends(self) -> None:
        backend = _FakeBackend({"/w/report.txt": b"data"})
        channel = _FakeChannel()
        tool = SendFile(channel, backend)
        chunk = await tool(
            path="/w/report.txt",
            target_id="1",
            target="user",
        )
        self.assertNotEqual(chunk.state, ToolResultState.ERROR)
        self.assertEqual(backend.read_calls, ["/w/report.txt"])
        self.assertEqual(len(channel.sent_files), 1)
        target_id, target, data, file_name = channel.sent_files[0]
        self.assertEqual(target_id, "1")
        self.assertEqual(target, "user")
        self.assertEqual(data, b"data")
        self.assertEqual(file_name, "report.txt")

    async def test_missing_file_returns_error(self) -> None:
        channel = _FakeChannel()
        tool = SendFile(channel, _FakeBackend())
        chunk = await tool(
            path="/nope/missing.txt",
            target_id="1",
            target="channel",
        )
        self.assertEqual(chunk.state, ToolResultState.ERROR)
        self.assertEqual(channel.sent_files, [])


class PermissionsTest(IsolatedAsyncioTestCase):
    """Read-only tools are allowed; send tools ask for confirmation."""

    async def test_read_only_tools_are_allowed(self) -> None:
        ctx = PermissionContext()
        for tool_cls in (ListChats, ListChatMembers):
            tool = tool_cls(_FakeChannel(), _FakeBackend())
            decision = await tool.check_permissions({}, ctx)
            self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_send_tools_ask(self) -> None:
        ctx = PermissionContext()
        for tool_cls in (SendMessage, SendFile):
            tool = tool_cls(_FakeChannel(), _FakeBackend())
            decision = await tool.check_permissions({}, ctx)
            self.assertEqual(decision.behavior, PermissionBehavior.ASK)


class ListToolsWiringTest(IsolatedAsyncioTestCase):
    """``DiscordChannel.list_tools`` wires the four tools to itself."""

    async def test_channel_exposes_four_tools(self) -> None:
        channel = DiscordChannel(
            "c1",
            DiscordChannel.Credentials(
                bot_token="t",
                application_id="a",
            ),
            DiscordChannel.Config(),
        )
        backend = _FakeBackend()
        tools = await channel.list_tools(_FakeWorkspace(backend))
        self.assertEqual(
            [type(t).__name__ for t in tools],
            ["ListChats", "ListChatMembers", "SendMessage", "SendFile"],
        )
        for tool in tools:
            self.assertIs(tool._channel, channel)
            self.assertIs(tool._backend, backend)
