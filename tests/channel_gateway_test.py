# -*- coding: utf-8 -*-
"""Tests for channel data-plane internals that stand alone from a live run.

Covers the channel's event-stream folding (``send_response`` driven off a
seeded event list via a fake channel), the gateway's media aggregation,
and the text-confirmation reply parser. Full two-phase orchestration
needs a running agent and is exercised end-to-end against a real bot.
"""
# pylint: disable=protected-access,missing-function-docstring,unused-argument
# pylint: disable=attribute-defined-outside-init
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._base import (
    ChannelBase,
    ChannelEvent,
    _EVENT_ADAPTER,
)
from agentscope.app.channel._gateway import ChannelGateway
from agentscope.message import Msg
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    SessionScope,
    SessionSettings,
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
        self.assertIn("hmm", _text(channel))

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
    """Storage stub capturing the workspace_id of upserted sessions."""

    def __init__(self) -> None:
        self.workspace_ids: list[str] = []

    async def get_session(self, **kwargs: Any) -> None:
        return None

    async def upsert_session(self, *, config: Any, **kwargs: Any) -> None:
        self.workspace_ids.append(config.workspace_id)


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
        created_at="t",
        updated_at="t",
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
