# -*- coding: utf-8 -*-
"""Tests for channel data-plane internals that stand alone from a live run.

Covers the channel's event-stream folding (``send_response`` driven off a
seeded event list via a fake channel), the gateway's media aggregation,
and the text-confirmation reply parser. Full two-phase orchestration
needs a running agent and is exercised end-to-end against a real bot.
"""
# pylint: disable=protected-access,missing-function-docstring,unused-argument
# pylint: disable=attribute-defined-outside-init
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._base import (
    ChannelBase,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    _EVENT_ADAPTER,
)
from agentscope.app.channel._gateway import ChannelGateway
from agentscope.app.channel._routing import resolve
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionConfig,
    SessionRecord,
    SessionScope,
    SessionSettings,
    SessionSource,
)
from agentscope.app.workspace_manager import (
    IsolationPolicy,
    WorkspaceManagerBase,
)
from agentscope.event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
)
from agentscope.message import DataBlock, TextBlock
from agentscope.message._block import Base64Source, URLSource
from agentscope.types import ReplyFinishedReason

_RID = "reply-1"


class _WM(WorkspaceManagerBase):
    """A workspace manager exercising only assign_workspace_id."""

    async def get_workspace(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def close(self, workspace_id: str) -> None:
        pass

    async def close_all(self) -> None:
        pass


def _event() -> ChannelEvent:
    return ChannelEvent(channel_id="chan-1", channel_user_id="u", chat_id="c")


async def _aiter(events: list) -> AsyncIterator[dict]:
    for evt in events:
        yield evt.model_dump(mode="json")


class _FakeChannel(ChannelBase):
    """A channel that records what ``send_response`` delivers."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "id"

    def __init__(self) -> None:
        self.delivered: list = []
        self.confirm: Any = None

    @property
    def channel_id(self) -> str:
        return "chan-1"

    async def start_listening(self, emit: Any) -> None:
        pass

    async def send_response(self, event: Any, events: Any) -> None:
        reply = None
        async for raw in events:
            evt = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(evt, RequireUserConfirmEvent):
                self.confirm = evt
                break
            reply_id = getattr(evt, "reply_id", None)
            if reply_id is not None:
                if reply is None:
                    reply = Msg(name="a", role="assistant", content=[])
                    reply.id = reply_id
                reply.append_event(evt)
            if isinstance(evt, ReplyEndEvent):
                break
        self.delivered.extend(
            self._render(
                reply,
                show_thinking=self._show_thinking,
                show_tool_process=self._show_tool_process,
            ),
        )


async def _run(events: list, **presentation: Any) -> _FakeChannel:
    channel = _FakeChannel()
    channel._show_tool_process = presentation.get("show_tool_process", False)
    channel._show_thinking = presentation.get("show_thinking", False)
    await channel.send_response(_event(), _aiter(events))
    return channel


def _text(channel: _FakeChannel) -> str:
    return "".join(
        b.text for b in channel.delivered if isinstance(b, TextBlock)
    )


def _text_blocks(*deltas: str) -> list:
    events: list = [TextBlockStartEvent(reply_id=_RID, block_id="t1")]
    events += [
        TextBlockDeltaEvent(reply_id=_RID, block_id="t1", delta=d)
        for d in deltas
    ]
    events.append(TextBlockEndEvent(reply_id=_RID, block_id="t1"))
    return events


class SendResponseTest(IsolatedAsyncioTestCase):
    """The event-stream accumulation (via Msg) + render in send_response."""

    async def test_text_reply(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                *_text_blocks("Hello ", "world"),
                ReplyEndEvent(session_id="s", reply_id=_RID),
            ],
        )
        self.assertEqual(_text(channel), "Hello world")
        self.assertIsNone(channel.confirm)

    async def test_confirm_delivers_text_then_presents(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                *_text_blocks("working"),
                RequireUserConfirmEvent(
                    id="req-1",
                    reply_id=_RID,
                    tool_calls=[],
                ),
                ReplyEndEvent(session_id="s", reply_id=_RID),  # not reached
            ],
        )
        self.assertEqual(_text(channel), "working")
        self.assertIsNotNone(channel.confirm)
        self.assertEqual(channel.confirm.id, "req-1")

    async def test_error_reply_end(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                ReplyEndEvent(
                    session_id="s",
                    reply_id=_RID,
                    finished_reason=ReplyFinishedReason.ERROR,
                ),
            ],
        )
        self.assertIn("error", _text(channel).lower())

    async def test_thinking_filtered_by_default(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                ThinkingBlockStartEvent(reply_id=_RID, block_id="k1"),
                ThinkingBlockDeltaEvent(
                    reply_id=_RID,
                    block_id="k1",
                    delta="hmm",
                ),
                ThinkingBlockEndEvent(reply_id=_RID, block_id="k1"),
                *_text_blocks("answer"),
                ReplyEndEvent(session_id="s", reply_id=_RID),
            ],
        )
        self.assertEqual(_text(channel), "answer")

    async def test_thinking_shown_when_enabled(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                ThinkingBlockStartEvent(reply_id=_RID, block_id="k1"),
                ThinkingBlockDeltaEvent(
                    reply_id=_RID,
                    block_id="k1",
                    delta="hmm",
                ),
                ThinkingBlockEndEvent(reply_id=_RID, block_id="k1"),
                *_text_blocks("answer"),
                ReplyEndEvent(session_id="s", reply_id=_RID),
            ],
            show_thinking=True,
        )
        # Markdown needs the blank line, or thinking runs into the answer.
        self.assertEqual(_text(channel), "\U0001f4ad hmm\n\nanswer")

    async def test_data_block_reassembled_and_delivered(self) -> None:
        channel = await _run(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                DataBlockStartEvent(
                    reply_id=_RID,
                    block_id="d1",
                    media_type="image/png",
                ),
                DataBlockDeltaEvent(
                    reply_id=_RID,
                    block_id="d1",
                    data="aW1n",
                    media_type="image/png",
                ),
                DataBlockEndEvent(reply_id=_RID, block_id="d1"),
                ReplyEndEvent(session_id="s", reply_id=_RID),
            ],
        )
        data = [b for b in channel.delivered if isinstance(b, DataBlock)]
        self.assertEqual(len(data), 1)
        self.assertIsInstance(data[0].source, Base64Source)
        self.assertEqual(data[0].source.data, "aW1n")
        self.assertEqual(data[0].source.media_type, "image/png")


class MediaBufferTest(IsolatedAsyncioTestCase):
    """Media-only messages buffer; a text message drains them."""

    def _img(self, name: str) -> DataBlock:
        return DataBlock(
            source=URLSource(
                url=f"https://example.com/{name}",
                media_type="image/png",
            ),
        )

    def _media_event(self, name: str) -> ChannelEvent:
        return ChannelEvent(
            channel_id="c",
            channel_user_id="u",
            chat_id="chat",
            content=[self._img(name)],
        )

    async def test_aggregate_media_only_buffers(self) -> None:
        bus = InMemoryMessageBus()
        gw = ChannelGateway(
            storage=None,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        self.assertIsNone(
            await gw._aggregate_media(self._media_event("a.png")),
        )

    async def test_aggregate_text_drains_buffered_media(self) -> None:
        bus = InMemoryMessageBus()
        gw = ChannelGateway(
            storage=None,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        await gw._aggregate_media(self._media_event("a.png"))
        await gw._aggregate_media(self._media_event("b.png"))
        content = await gw._aggregate_media(
            ChannelEvent(
                channel_id="c",
                channel_user_id="u",
                chat_id="chat",
                content=[TextBlock(text="look")],
            ),
        )
        assert content is not None
        self.assertEqual(len(content), 3)  # two buffered images + text
        self.assertIsInstance(content[0], DataBlock)
        self.assertIsInstance(content[-1], TextBlock)


class _RecordingStorage:
    """Storage stub capturing what a session was upserted with."""

    def __init__(self) -> None:
        self.workspace_ids: list[str] = []
        self.upserts: list[dict[str, Any]] = []

    async def get_session(self, **kwargs: Any) -> None:
        return None

    async def upsert_session(self, *, config: Any, **kwargs: Any) -> None:
        self.workspace_ids.append(config.workspace_id)
        self.upserts.append(kwargs)


class _InboundStorage(_RecordingStorage):
    """Storage stub for one normal inbound channel message."""

    def __init__(
        self,
        record: ChannelRecord,
        session: SessionRecord | None = None,
    ) -> None:
        super().__init__()
        self.record = record
        self.session = session

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        """Return the configured channel by id."""
        return self.record if channel_id == self.record.id else None

    async def get_session(self, **kwargs: Any) -> SessionRecord | None:
        """Return the optional existing channel session."""
        del kwargs
        return self.session


def _channel_record(user_id: str) -> ChannelRecord:
    return ChannelRecord(
        id="chan-1",
        channel_type="feishu",
        user_id=user_id,
        routing=RoutingConfig(
            bindings=[ChannelBinding(match_value="*", agent_id="agent-x")],
        ),
        session=SessionSettings(
            chat_model_config={
                "type": "openai_chat",
                "credential_id": "cred-1",
                "model": "gpt-4",
                "parameters": {},
            },
        ),
    )


class WorkspaceIsolationTest(IsolatedAsyncioTestCase):
    """Channel-created sessions get isolated workspaces, not a shared one."""

    async def test_distinct_users_get_distinct_workspaces(self) -> None:
        storage = _RecordingStorage()
        gw = ChannelGateway(
            storage=storage,
            message_bus=InMemoryMessageBus(),
            workspace_manager=_WM(isolation=IsolationPolicy.PER_USER),
        )
        await gw._ensure_session(
            _channel_record("user-a"),
            "agent-x",
            "s-a",
            ChannelEvent(
                channel_id="c",
                channel_user_id="u",
                chat_id="chat-a",
            ),
            SessionScope.PER_CHAT,
        )
        await gw._ensure_session(
            _channel_record("user-b"),
            "agent-x",
            "s-b",
            ChannelEvent(
                channel_id="c",
                channel_user_id="u",
                chat_id="chat-b",
            ),
            SessionScope.PER_CHAT,
        )
        self.assertEqual(len(storage.workspace_ids), 2)
        # Different owners must not alias the same workspace.
        self.assertNotEqual(
            storage.workspace_ids[0],
            storage.workspace_ids[1],
        )


class TrustedChannelIdentityTest(IsolatedAsyncioTestCase):
    """The gateway stores the trusted sender on the channel session."""

    async def test_session_contains_trusted_platform_user(
        self,
    ) -> None:
        record = _channel_record("owner-1")
        storage = _InboundStorage(record)
        bus = InMemoryMessageBus()
        gateway = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        await gateway.process(
            ChannelEvent(
                channel_id=record.id,
                channel_user_id="staff-1",
                chat_id="group:cid-1",
                content=[TextBlock(text="hello")],
            ),
        )

        queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(len(queued), 1)
        payload = queued[0][1]
        self.assertEqual(payload["input"]["name"], "staff-1")
        self.assertNotIn("channel_user_id", payload)
        self.assertEqual(
            storage.upserts[0]["source_channel_user_id"],
            "staff-1",
        )

    async def test_existing_session_tracks_latest_inbound_user(self) -> None:
        """A fresh turn refreshes the user stored on a shared session."""
        record = _channel_record("owner-1")
        _, session_id, _ = resolve(
            ChannelEvent(
                channel_id=record.id,
                channel_user_id="staff-2",
                chat_id="group:cid-1",
            ),
            record,
        )
        session = SessionRecord(
            id=session_id,
            user_id=record.user_id,
            agent_id="agent-x",
            source=SessionSource.CHANNEL,
            source_channel_id=record.id,
            source_channel_user_id="staff-1",
            config=SessionConfig(workspace_id="ws-1"),
        )
        storage = _InboundStorage(record, session)
        gateway = ChannelGateway(
            storage=storage,
            message_bus=InMemoryMessageBus(),
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        await gateway.process(
            ChannelEvent(
                channel_id=record.id,
                channel_user_id="staff-2",
                chat_id="group:cid-1",
                content=[TextBlock(text="hello")],
            ),
        )

        self.assertEqual(len(storage.upserts), 1)
        self.assertEqual(
            storage.upserts[0]["source_channel_user_id"],
            "staff-2",
        )


class FeishuPostParseTest(IsolatedAsyncioTestCase):
    """Feishu rich-text ``post`` flattens to ordered text + data blocks."""

    async def test_mixed_text_image_link(self) -> None:
        from agentscope.app.channel._feishu._channel import FeishuChannel

        channel = FeishuChannel(
            "c",
            FeishuChannel.Credentials(app_id="a", app_secret="s"),
            FeishuChannel.Config(),
        )

        async def _fake_download(
            message_id: str,
            key: str,
            resource_type: str,
            default_mime: str,
            name: str,
        ) -> DataBlock:
            return DataBlock(
                source=Base64Source(data="aW1n", media_type=default_mime),
                name=name,
            )

        setattr(channel, "_download_resource", _fake_download)
        post = {
            "title": "T",
            "content": [
                [
                    {"tag": "text", "text": "hello "},
                    {"tag": "img", "image_key": "img-1"},
                ],
                [{"tag": "a", "text": "link", "href": "http://x"}],
            ],
        }
        blocks = await channel._parse_post(post, "m1")
        self.assertIsInstance(blocks[0], TextBlock)
        self.assertIn("hello", blocks[0].text)
        self.assertIsInstance(blocks[1], DataBlock)
        self.assertEqual(blocks[1].source.data, "aW1n")
        self.assertTrue(
            any(isinstance(b, TextBlock) and "link" in b.text for b in blocks),
        )


class _AwaitingStorage:
    """Storage stub whose one session is parked on a tool call."""

    def __init__(self, record: ChannelRecord, session_id: str) -> None:
        self._record = record
        self._session_id = session_id
        self.asked: list[str] = []

    async def get_channel(self, channel_id: str) -> ChannelRecord:
        del channel_id
        return self._record

    async def list_sessions_by_channel(
        self,
        user_id: str,
        channel_id: str,
    ) -> list[Any]:
        del user_id, channel_id
        return [
            SessionRecord(
                id=self._session_id,
                user_id=self._record.user_id,
                agent_id="agent-x",
                source_chat_id="group:cid-1",
                config=SessionConfig(workspace_id="ws-1"),
            ),
        ]

    async def get_session(self, *, session_id: str, **kwargs: Any) -> Any:
        self.asked.append(session_id)
        if session_id != self._session_id:
            return None
        return SessionRecord(
            id=session_id,
            user_id=self._record.user_id,
            agent_id="agent-x",
            config=SessionConfig(workspace_id="ws-1"),
            state=AgentState(
                reply_id="reply-1",
                context=[
                    Msg(
                        name="Friday",
                        role="assistant",
                        content=[
                            ToolCallBlock(
                                type="tool_call",
                                id="call_abc",
                                name="Bash",
                                input="{}",
                                state=ToolCallState.ASKING,
                            ),
                        ],
                    ),
                ],
            ),
        )

    async def get_agent(self, **kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(data=SimpleNamespace(name="Friday"))


class ChatNameRecordingTest(IsolatedAsyncioTestCase):
    """The title arrives with the message; a later node cannot look it up."""

    async def _upsert(self, chat_name: str) -> dict[str, Any]:
        storage = _RecordingStorage()
        gw = ChannelGateway(
            storage=storage,
            message_bus=InMemoryMessageBus(),
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        await gw._ensure_session(
            _channel_record("user-a"),
            "agent-x",
            "s-a",
            ChannelEvent(
                channel_id="c",
                channel_user_id="u",
                chat_id="group:cid-1",
                chat_name=chat_name,
            ),
            SessionScope.PER_CHAT,
        )
        return storage.upserts[0]

    async def test_chat_title_is_recorded_on_the_session(self) -> None:
        upsert = await self._upsert("产品群")

        self.assertEqual(upsert["source_chat_id"], "group:cid-1")
        self.assertEqual(upsert["source_chat_name"], "产品群")

    async def test_a_nameless_chat_records_no_title(self) -> None:
        """A private chat has no title, and "" is not one."""
        upsert = await self._upsert("")

        self.assertIsNone(upsert["source_chat_name"])


class DecisionRoutingTest(IsolatedAsyncioTestCase):
    """A click resumes the run that is waiting, not the one routing picks."""

    async def test_decision_finds_the_waiting_session(self) -> None:
        record = _channel_record("user-1")
        # Not what routing derives: the platform names the clicker
        # differently than it named the sender.
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        await gw.process(
            ChannelConfirmationResultEvent(
                channel_id="chan-1",
                chat_id="group:cid-1",
                channel_user_id="300905",
                tool_call_id="call_abc",
                approved=True,
                actor="300905",
            ),
        )

        queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(len(queued), 1)
        payload = queued[0][1]
        event = payload["input"]
        tool_call = event["confirm_results"][0]["tool_call"]
        self.assertDictEqual(
            payload,
            {
                "user_id": "user-1",
                "session_id": "the-parked-session",
                "agent_id": "agent-x",
                "kind": MessageBusKeys.WAKEUP_KIND_RESUME,
                "input": {
                    "id": event["id"],
                    "created_at": event["created_at"],
                    "metadata": {},
                    "type": "USER_CONFIRM_RESULT",
                    "reply_id": "reply-1",
                    "confirm_results": [
                        {
                            "confirmed": True,
                            "rules": None,
                            "tool_call": {
                                "type": "tool_call",
                                "id": "call_abc",
                                "name": "Bash",
                                "input": "{}",
                                "state": "asking",
                                "suggested_rules": [],
                                "created_at": tool_call["created_at"],
                                "finished_at": None,
                            },
                        },
                    ],
                },
            },
        )
        # The routing guess was tried first, then the parked session.
        self.assertNotEqual(storage.asked[0], "the-parked-session")
        self.assertIn("the-parked-session", storage.asked)
