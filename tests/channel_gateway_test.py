# -*- coding: utf-8 -*-
"""Tests for channel data-plane internals that stand alone from a live run.

Covers the channel's event-stream folding (``send_response`` driven off a
seeded event list via a fake channel), the gateway's media aggregation,
and the text-confirmation reply parser. Full two-phase orchestration
needs a running agent and is exercised end-to-end against a real bot.
"""
# pylint: disable=protected-access,missing-function-docstring,unused-argument
# pylint: disable=attribute-defined-outside-init
import asyncio
import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis

from agentscope.app._bus_ops import (
    has_pending_inbox_or_release,
    register_inbox_consumer,
    session_execution,
    list_chat_inputs,
)
from agentscope.app.channel import (
    ChannelClients,
    ChannelTypeRegistry,
    FeishuChannel,
)
from agentscope.app.channel._dingtalk._channel import DingTalkChannel
from agentscope.app.channel._base import (
    ChannelBase,
    ChannelAuthConfirmationResultEvent,
    ChannelConfirmationResultEvent,
    ChannelDecisionStatus,
    ChannelEvent,
    _EVENT_ADAPTER,
)
from agentscope.app.channel._approval import (
    ChannelApprovalRecord,
    load_approval,
    new_approval_id,
    store_approval,
    remember_reply_requester,
)
from agentscope.app.channel._gateway import ChannelGateway
from agentscope.app.channel._routing import resolve
from agentscope.message import Msg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState
from agentscope.app.middleware import InboxMiddleware
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    MessageBusKeys,
    RedisMessageBus,
)
from agentscope.app.message_bus import _in_memory_message_bus as _bus
from agentscope.app.storage import (
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    ChannelOrigin,
    SessionConfig,
    SessionRecord,
    SessionScope,
    SessionSettings,
)
from agentscope.app.workspace_manager import (
    IsolationPolicy,
    WorkspaceManagerBase,
)
from agentscope.event import (
    EventType,
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

    def _caption_fixture(
        self,
        bus: MessageBus | None = None,
    ) -> tuple[MessageBus, ChannelGateway, ChannelEvent, ChannelEvent, str]:
        record = _channel_record("owner")
        if bus is None:
            bus = InMemoryMessageBus()
        gateway = ChannelGateway(
            storage=_InboundStorage(record),
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        image = ChannelEvent(
            channel_id=record.id,
            channel_user_id="u",
            chat_id="chat",
            content=[self._img("a.png")],
        )
        caption = image.model_copy(
            update={"content": [TextBlock(text="look")]},
        )
        _, session_id, _ = resolve(caption, record)
        return bus, gateway, image, caption, session_id

    async def test_aggregate_media_only_buffers(self) -> None:
        bus, gateway, image, _, sid = self._caption_fixture()
        await gateway.process(image)
        buffer = MessageBusKeys.channel_media_buffer(
            image.channel_id,
            "chat",
            "u",
        )
        self.assertEqual(
            (
                [payload for _, payload in await bus.queue_read(buffer)],
                await list_chat_inputs(bus, sid),
            ),
            ([image.content[0].model_dump(mode="json")], []),
        )

    async def test_full_queue_keeps_media_for_caption_retry(self) -> None:
        bus, gateway, image, caption, session_id = self._caption_fixture()
        channel = _FakeChannel()
        channel._show_thinking = channel._show_tool_process = False
        queue = MessageBusKeys.chat_inputs(session_id)
        buffer = MessageBusKeys.channel_media_buffer(
            image.channel_id,
            "chat",
            "u",
        )
        await gateway.process(image, channel=channel)
        buffered = await bus.queue_read(buffer)
        with patch.object(MessageBusKeys, "CHAT_INPUT_MAX_LEN", 1):
            await bus.queue_push(queue, {"id": "existing"})
            await gateway.process(caption, channel=channel)
            self.assertEqual(
                _text(channel),
                "The message queue is full. "
                "Please resend your message shortly.",
            )
            self.assertEqual(await bus.queue_read(buffer), buffered)
            self.assertEqual(
                [data for _, data in await bus.queue_drain(queue)],
                [{"id": "existing"}],
            )
            await gateway.process(caption, channel=channel)
        (queued,) = await list_chat_inputs(bus, session_id)
        self.assertEqual(
            (
                Msg.model_validate(queued["input"]).content,
                await bus.queue_read(buffer),
            ),
            ([*image.content, *caption.content], []),
        )

    async def test_caption_does_not_depend_on_separate_media_cleanup(
        self,
    ) -> None:
        bus, gateway, image, caption, sid = self._caption_fixture()
        await gateway.process(image)
        with patch.object(
            bus,
            "queue_drain",
            side_effect=ConnectionError("cleanup failed"),
        ):
            await gateway.process(caption)
        following = caption.model_copy(
            update={"content": [TextBlock(text="next")]},
        )
        await gateway.process(following)
        self.assertEqual(
            [
                Msg.model_validate(item["input"]).content
                for item in await list_chat_inputs(bus, sid)
            ],
            [[*image.content, *caption.content], following.content],
        )

    async def test_caption_survives_lost_commit_response(self) -> None:
        bus, gateway, image, caption, sid = self._caption_fixture()
        await gateway.process(image)
        push = bus.queue_push_consuming

        async def lose_response(*args: Any) -> bool:
            self.assertTrue(await push(*args))
            raise ConnectionError("Lost reply to enqueue")

        with (
            patch.object(bus, "queue_push_consuming", lose_response),
            self.assertRaises(ConnectionError),
        ):
            await gateway._handle_message(caption)
        following = caption.model_copy(
            update={"content": [TextBlock(text="next")]},
        )
        await gateway.process(following)
        self.assertEqual(
            [
                Msg.model_validate(item["input"]).content
                for item in await list_chat_inputs(bus, sid)
            ],
            [[*image.content, *caption.content], following.content],
        )

    async def test_redis_retry_after_agent_consumes_channel_caption(
        self,
    ) -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.addAsyncCleanup(client.aclose)
        bus = await self.enterAsyncContext(
            RedisMessageBus(connection_pool=client.connection_pool),
        )
        _, gateway, image, caption, sid = self._caption_fixture(bus)
        channel = _FakeChannel()
        agent = SimpleNamespace(name="agent", state=AgentState(session_id=sid))
        middleware = InboxMiddleware(bus)
        execute = bus.get_client().eval
        inbox = MessageBusKeys.inbox(sid)
        receipts = []

        async def lose_first_response(*args: Any) -> int:
            accepted = await execute(*args)
            if args[1] > 1 and args[2] == inbox:
                receipts.append(accepted)
                async for _ in middleware.on_reasoning(
                    agent,
                    {},
                    lambda: _aiter([]),
                ):
                    pass
                accepted = await execute(*args)
                receipts.append(accepted)
            return accepted

        await gateway.process(image, channel=channel)
        async with session_execution(bus, sid):
            await register_inbox_consumer(bus, sid)
            with (
                patch.object(bus.get_client(), "eval", lose_first_response),
                patch.object(
                    channel,
                    "send_response",
                    new_callable=AsyncMock,
                ) as notice,
            ):
                await gateway.process(caption, channel=channel)
        self.assertEqual(
            (
                receipts,
                [
                    block.hint
                    for msg in agent.state.context
                    for block in msg.get_content_blocks("hint")
                ],
                await bus.queue_read(inbox),
                notice.await_count,
            ),
            ([1, 1], [[*image.content, *caption.content]], [], 0),
        )

    async def test_expired_media_rejects_caption_before_publication(
        self,
    ) -> None:
        bus, gateway, image, caption, sid = self._caption_fixture()
        await gateway.process(image)
        push = bus.queue_push_consuming

        async def expire_media(
            key: str,
            payload: dict,
            source_key: str,
            entry_ids: list[str],
        ) -> bool:
            await bus.queue_delete(source_key)
            return await push(key, payload, source_key, entry_ids)

        channel = _FakeChannel()
        channel._show_thinking = channel._show_tool_process = False
        with patch.object(bus, "queue_push_consuming", expire_media):
            await gateway.process(caption, channel=channel)
        self.assertEqual(
            (
                await list_chat_inputs(bus, sid),
                await bus.registry_getall(
                    MessageBusKeys.chat_input_payloads(sid),
                ),
                await bus.queue_read(MessageBusKeys.wakeup_queue()),
                _text(channel),
            ),
            (
                [],
                {},
                [],
                "The selected attachments are no longer available. "
                "Please resend them with your message.",
            ),
        )

    async def test_cancel_after_enqueue_consumes_only_caption_media(
        self,
    ) -> None:
        bus, gateway, image, caption, session_id = self._caption_fixture()
        unrelated = caption.model_copy(
            update={"content": [TextBlock(text="next")]},
        )
        waking = asyncio.Event()

        async def wait_for_wakeup(*args: Any, **kwargs: Any) -> None:
            waking.set()
            await asyncio.Future()

        await gateway.process(image)
        with patch(
            "agentscope.app._bus_ops.enqueue_run_trigger",
            wait_for_wakeup,
        ):
            task = asyncio.create_task(gateway.process(caption))
            await asyncio.wait_for(waking.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await gateway.process(unrelated)
        self.assertEqual(
            [
                Msg.model_validate(item["input"]).content
                for item in await list_chat_inputs(bus, session_id)
            ],
            [[*image.content, *caption.content], unrelated.content],
        )

    async def test_cancel_before_enqueue_keeps_caption_media(self) -> None:
        bus, gateway, image, caption, sid = self._caption_fixture()
        preparing = asyncio.Event()
        registry_set = bus.registry_set

        async def hold_payload(key: str, *args: Any, **kwargs: Any) -> None:
            if key == MessageBusKeys.chat_input_payloads(sid):
                preparing.set()
                await asyncio.Future()
            await registry_set(key, *args, **kwargs)

        await gateway.process(image)
        with patch.object(bus, "registry_set", hold_payload):
            task = asyncio.create_task(gateway.process(caption))
            await asyncio.wait_for(preparing.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await list_chat_inputs(bus, sid), [])
        await gateway.process(caption)
        (item,) = await list_chat_inputs(bus, sid)
        self.assertEqual(
            Msg.model_validate(item["input"]).content,
            [*image.content, *caption.content],
        )

    async def test_inbox_cancellation_after_commit_keeps_media_consumed(
        self,
    ) -> None:
        bus, gateway, image, caption, sid = self._caption_fixture()
        committed = asyncio.Event()
        push = bus.queue_push_consuming

        async def hold_response(*args: Any) -> bool:
            self.assertTrue(await push(*args))
            committed.set()
            await asyncio.Future()
            return True

        await gateway.process(image)
        async with session_execution(bus, sid):
            await register_inbox_consumer(bus, sid)
            with patch.object(bus, "queue_push_consuming", hold_response):
                task = asyncio.create_task(gateway.process(caption))
                await asyncio.wait_for(committed.wait(), timeout=2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            unrelated = caption.model_copy(
                update={"content": [TextBlock(text="next")]},
            )
            await gateway.process(unrelated)
        self.assertEqual(
            [
                hint["hint"]
                for _, hint in await bus.queue_drain(MessageBusKeys.inbox(sid))
            ],
            [
                [b.model_dump(mode="json") for b in blocks]
                for blocks in (
                    [*image.content, *caption.content],
                    unrelated.content,
                )
            ],
        )

    async def test_busy_queue_keeps_media_and_notifies_sender(self) -> None:
        for session_lock in (True, False):
            with self.subTest(session_lock=session_lock):
                bus, gateway, image, caption, sid = self._caption_fixture()
                key = (
                    MessageBusKeys.chat_input_mutation_lock(sid)
                    if session_lock
                    else MessageBusKeys.chat_input_user_quota_lock("owner")
                )
                channel = _FakeChannel()
                channel._show_thinking = channel._show_tool_process = False
                await gateway.process(image)
                with patch.object(
                    MessageBusKeys,
                    "CHAT_INPUT_MUTATION_TIMEOUT_SECS",
                    0.02,
                ):
                    async with bus.acquire_lock(key):
                        await gateway.process(caption, channel=channel)
                self.assertEqual(
                    (_text(channel), await list_chat_inputs(bus, sid)),
                    (
                        "The message queue is busy. "
                        "Please resend your message shortly.",
                        [],
                    ),
                )
                await gateway.process(caption, channel=channel)
                (item,) = await list_chat_inputs(bus, sid)
                self.assertEqual(
                    Msg.model_validate(item["input"]).content,
                    [*image.content, *caption.content],
                )

    async def test_new_media_refreshes_buffer_for_earlier_media(self) -> None:
        """All buffered images survive until the last push's deadline."""
        bus, gateway, first, caption, sid = self._caption_fixture()
        second = first.model_copy(update={"content": [self._img("b.png")]})
        with patch.object(_bus.time, "monotonic", return_value=0):
            await gateway.process(first)
        with patch.object(_bus.time, "monotonic", return_value=240):
            await gateway.process(second)
        with patch.object(_bus.time, "monotonic", return_value=360):
            await gateway.process(caption)
        (queued,) = await list_chat_inputs(bus, sid)
        self.assertEqual(
            Msg.model_validate(queued["input"]).content,
            [*first.content, *second.content, *caption.content],
        )


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

    def __init__(self, record: ChannelRecord) -> None:
        super().__init__()
        self.record = record

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        """Return the configured channel by id."""
        return self.record if channel_id == self.record.id else None


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


class _ChannelStorage:
    """Return one channel record for the gateway hand-off test."""

    def __init__(self, record: ChannelRecord) -> None:
        self._record = record

    async def get_channel(self, channel_id: str) -> ChannelRecord:
        del channel_id
        return self._record


class _PausedSessionCheckBus(InMemoryMessageBus):
    """Pause the gateway after it observes the active session lock."""

    def __init__(self, lock_key: str) -> None:
        super().__init__()
        self._lock_key = lock_key
        self.checked = asyncio.Event()
        self.resume = asyncio.Event()

    async def is_locked(self, key: str) -> bool:
        locked = await super().is_locked(key)
        if key == self._lock_key:
            self.checked.set()
            await self.resume.wait()
        return locked


class ChannelInboxHandoffTest(IsolatedAsyncioTestCase):
    """Channel hints must use the session inbox hand-off protocol."""

    async def test_late_channel_message_starts_an_identified_user_turn(
        self,
    ) -> None:
        """A message after the final consumer check cannot be stranded."""
        record = _channel_record("user-1")
        event = ChannelEvent(
            channel_id="chan-1",
            channel_user_id="member-1",
            chat_id="chat-1",
            content=[TextBlock(text="late message")],
        )
        _agent_id, session_id, _scope = resolve(event, record)
        bus = _PausedSessionCheckBus(
            MessageBusKeys.session_execution_lock(session_id),
        )
        gateway = ChannelGateway(
            storage=_InboundStorage(record),
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        await register_inbox_consumer(bus, session_id)
        async with session_execution(bus, session_id):
            message_task = asyncio.create_task(gateway.process(event))
            await asyncio.wait_for(bus.checked.wait(), timeout=2)

            self.assertFalse(
                await has_pending_inbox_or_release(bus, session_id),
            )
            bus.resume.set()
            await asyncio.wait_for(message_task, timeout=2)

        ((_, trigger),) = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        (queued,) = await list_chat_inputs(bus, session_id)
        message = Msg.model_validate(queued["input"])
        ((_, item),) = await bus.queue_read(
            MessageBusKeys.chat_inputs(session_id),
        )
        self.assertEqual(
            (
                trigger["kind"],
                message.name,
                message.role,
                message.content,
                item["channel_requester_id"],
                await bus.queue_drain(MessageBusKeys.inbox(session_id)),
            ),
            (
                "queued_message",
                event.channel_user_id,
                "user",
                event.content,
                event.channel_user_id,
                [],
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
    """The gateway records the trusted sender on the session's origin."""

    async def test_session_origin_carries_the_trusted_sender(self) -> None:
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
                chat_name="Product",
                content=[TextBlock(text="hello")],
            ),
        )

        self.assertEqual(
            storage.upserts[0]["origin"],
            ChannelOrigin(
                channel_id=record.id,
                chat_id="group:cid-1",
                chat_name="Product",
                channel_user_id="staff-1",
            ),
        )
        queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(len(queued), 1)
        self.assertNotIn("channel_user_id", queued[0][1])

    async def test_dingtalk_requester_requires_staff_id(self) -> None:
        for staff_id in ("staff-1", ""):
            with self.subTest(staff_id=staff_id):
                record = _channel_record("owner-1")
                storage = _InboundStorage(record)
                bus = InMemoryMessageBus()
                gateway = ChannelGateway(
                    storage=storage,
                    message_bus=bus,
                    workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
                )
                channel = DingTalkChannel(
                    record.id,
                    DingTalkChannel.Credentials(
                        client_id="app",
                        client_secret="secret",
                    ),
                    DingTalkChannel.Config(),
                )

                channel._emit = gateway.process
                payload = {
                    "conversationType": "2",
                    "conversationId": "cid-1",
                    "senderId": "encrypted-sender-1",
                    "msgtype": "text",
                    "text": {"content": "hello"},
                }
                if staff_id:
                    payload["senderStaffId"] = staff_id
                await channel._on_callback(payload)

                [(_, trigger)] = await bus.queue_drain(
                    MessageBusKeys.wakeup_queue(),
                )
                (queued,) = await list_chat_inputs(bus, trigger["session_id"])
                message = Msg.model_validate(queued["input"])
                self.assertEqual(message.name, staff_id or payload["senderId"])
                ((_, item),) = await bus.queue_read(
                    MessageBusKeys.chat_inputs(trigger["session_id"]),
                )
                self.assertEqual(
                    item.get("channel_requester_id", ""),
                    staff_id,
                )


class FeishuPostParseTest(IsolatedAsyncioTestCase):
    """Feishu rich-text ``post`` flattens to ordered text + data blocks."""

    async def test_mixed_text_image_link(self) -> None:
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

    async def test_code_block_is_kept(self) -> None:
        """Code retains its whitespace and position relative to prose."""
        channel = FeishuChannel(
            "c",
            FeishuChannel.Credentials(app_id="a", app_secret="s"),
            FeishuChannel.Config(),
        )
        code = "    first()\n    second()\n"
        snippet = [{"tag": "code_block", "language": "python", "text": code}]
        intro = [{"tag": "text", "text": "Explain:"}]
        outro = [{"tag": "text", "text": "Why?"}]
        for rows, expected in (
            ([snippet], [code]),
            ([intro, snippet, outro], ["Explain:", code, "Why?"]),
            ([[{"tag": "code_block", "text": "  \n"}]], []),
        ):
            with self.subTest(rows=rows):
                blocks = await channel._parse_post({"content": rows}, "m1")
                self.assertEqual(
                    [block.model_dump() for block in blocks],
                    [
                        {
                            "type": "text",
                            "text": text,
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        }
                        for text in expected
                    ],
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
                origin=ChannelOrigin(
                    channel_id="chan-1",
                    chat_id="group:cid-1",
                ),
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
                        id="reply-1",
                        metadata={
                            "pending_confirmations": {"call_abc": "current"},
                        },
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

        self.assertEqual(
            upsert["origin"],
            ChannelOrigin(
                channel_id="chan-1",
                chat_id="group:cid-1",
                chat_name="产品群",
                channel_user_id="u",
            ),
        )

    async def test_a_nameless_chat_records_no_title(self) -> None:
        """A private chat has no title, and "" is not one."""
        upsert = await self._upsert("")

        self.assertEqual(
            upsert["origin"],
            ChannelOrigin(
                channel_id="chan-1",
                chat_id="group:cid-1",
                chat_name=None,
                channel_user_id="u",
            ),
        )


class DecisionRoutingTest(IsolatedAsyncioTestCase):
    """Approval routing and authorization come only from server state."""

    async def test_dingtalk_callback_uses_saved_approval(
        self,
    ) -> None:
        """Use the card ID and enforce channel and requester checks."""
        for private, action, actor, conversation, channel_id in (
            (True, "agree", "requester-1", "cid-origin", "chan-1"),
            (True, "reject", "requester-1", "cid-origin", "chan-1"),
            (False, "agree", "requester-1", "cid-origin", "chan-1"),
            (True, "agree", "other-user", "cid-origin", "chan-1"),
            (True, "agree", "requester-1", "cid-other", "chan-1"),
            (True, "agree", "requester-1", "", "chan-1"),
            (True, "agree", "requester-1", "cid-origin", "other-channel"),
        ):
            with self.subTest(
                private=private,
                action=action,
                actor=actor,
                conversation=conversation,
                channel_id=channel_id,
            ):
                record = _channel_record("owner-1").model_copy(
                    update={"channel_type": "dingtalk"},
                )
                storage = _AwaitingStorage(record, "the-parked-session")
                bus = InMemoryMessageBus()
                gateway = ChannelGateway(
                    storage=storage,
                    message_bus=bus,
                    workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
                )
                clients = ChannelClients(storage, bus, ChannelTypeRegistry([]))
                await remember_reply_requester(
                    bus,
                    session_id="the-parked-session",
                    reply_id="reply-1",
                    requester_id="requester-1",
                )
                chat_id = "user:requester-1" if private else "group:cid-origin"

                async def events() -> AsyncIterator[dict]:
                    yield {
                        "type": EventType.REQUIRE_USER_CONFIRM,
                        "reply_id": "reply-1",
                        "id": "current",
                        "tool_calls": [{"id": "call_abc"}],
                    }

                [confirmation] = [
                    raw
                    async for raw in clients._approval_events(
                        events(),
                        channel_id="chan-1",
                        chat_id=chat_id,
                        agent_id="agent-x",
                        session_id="the-parked-session",
                    )
                ]
                approval_id = confirmation["metadata"]["channel_approval_ids"][
                    "call_abc"
                ]
                channel = DingTalkChannel(
                    channel_id,
                    DingTalkChannel.Credentials(
                        client_id="app",
                        client_secret="secret",
                    ),
                    DingTalkChannel.Config(),
                )
                api = SimpleNamespace(
                    send_text=AsyncMock(return_value=True),
                    update_approval_card=AsyncMock(return_value=True),
                )
                channel._openapi = api
                channel._emit = gateway.process
                payload = {
                    "type": "actionCallback",
                    "outTrackId": approval_id,
                    "userId": actor,
                    "spaceType": "im",
                    "spaceId": conversation,
                    "content": json.dumps(
                        {
                            "cardPrivateData": {
                                "actionIds": ["single_button_node"],
                                "params": {
                                    "id": action,
                                    "chatId": "group:untrusted",
                                    "approvalId": "untrusted",
                                },
                            },
                        },
                    ),
                }
                await channel._on_card_callback(payload)
                queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
                accepted = actor == "requester-1" and channel_id == "chan-1"
                if accepted:
                    self.assertEqual(len(queued), 1)
                    self.assertEqual(
                        queued[0][1]["input"]["confirm_results"][0][
                            "confirmed"
                        ],
                        action == "agree",
                    )
                    self.assertIsNone(await load_approval(bus, approval_id))
                    api.update_approval_card.assert_awaited_once()
                    # A repeat callback cannot queue another decision.
                    await channel._on_card_callback(payload)
                    self.assertEqual(
                        await bus.queue_drain(MessageBusKeys.wakeup_queue()),
                        [],
                    )
                else:
                    self.assertEqual(queued, [])
                    self.assertIsNotNone(await load_approval(bus, approval_id))
                    api.update_approval_card.assert_not_awaited()
                    if actor == "other-user":
                        self.assertEqual(
                            api.send_text.await_args.args[0],
                            chat_id,
                        )
                    else:
                        api.send_text.assert_not_awaited()

    @staticmethod
    async def _approval(
        bus: InMemoryMessageBus,
        confirmation_id: str | None = "current",
    ) -> str:
        approval_id = new_approval_id("the-parked-session")
        await store_approval(
            bus,
            approval_id,
            ChannelApprovalRecord(
                channel_id="chan-1",
                chat_id="group:cid-1",
                agent_id="agent-x",
                session_id="the-parked-session",
                reply_id="reply-1",
                tool_call_id="call_abc",
                confirmation_id=confirmation_id,
                requester_id="requester-1",
            ),
        )
        return approval_id

    async def test_decision_finds_the_waiting_session(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        decision = ChannelConfirmationResultEvent(
            channel_id="chan-1",
            chat_id="",
            channel_user_id="requester-1",
            approved=True,
            actor="requester-1",
            approval_id=approval_id,
        )
        wrong_chat = decision.model_copy()
        wrong_chat.chat_id = "group:other"
        self.assertIs(
            await gw.process(wrong_chat),
            ChannelDecisionStatus.STALE,
        )

        status = await gw.process(decision)
        self.assertEqual(decision.chat_id, "group:cid-1")

        self.assertIs(status, ChannelDecisionStatus.ACCEPTED)
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
                            "request_id": "current",
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
        self.assertEqual(storage.asked, ["the-parked-session"])
        self.assertIsNone(await load_approval(bus, approval_id))

    async def test_superseded_cards_do_not_consume_the_current_prompt(
        self,
    ) -> None:
        storage = _AwaitingStorage(
            _channel_record("user-1"),
            "the-parked-session",
        )
        bus = InMemoryMessageBus()
        gateway = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        statuses = []
        for prompt in ("previous", None, "current"):
            event = ChannelConfirmationResultEvent(
                channel_id="chan-1",
                chat_id="group:cid-1",
                channel_user_id="requester-1",
                approval_id=await self._approval(bus, prompt),
                approved=True,
            )
            statuses.append(await gateway.process(event))
        statuses.append(await gateway.process(event))
        queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(
            (
                statuses,
                [
                    item["input"]["confirm_results"][0]["request_id"]
                    for _, item in queued
                ],
            ),
            (
                [
                    ChannelDecisionStatus.STALE,
                    ChannelDecisionStatus.STALE,
                    ChannelDecisionStatus.ACCEPTED,
                    ChannelDecisionStatus.STALE,
                ],
                ["current"],
            ),
        )

    async def test_non_requester_is_rejected_without_consuming_card(
        self,
    ) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        status = await gw.process(
            ChannelConfirmationResultEvent(
                channel_id="chan-1",
                chat_id="group:cid-1",
                channel_user_id="other-user",
                approved=False,
                actor="other-user",
                approval_id=approval_id,
            ),
        )

        self.assertIs(status, ChannelDecisionStatus.UNAUTHORIZED)
        self.assertEqual(
            await bus.queue_drain(MessageBusKeys.wakeup_queue()),
            [],
        )
        self.assertIsNotNone(await load_approval(bus, approval_id))
        self.assertEqual(storage.asked, [])

    async def test_missing_requester_fails_closed(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = new_approval_id("the-parked-session")
        await store_approval(
            bus,
            approval_id,
            ChannelApprovalRecord(
                channel_id="chan-1",
                chat_id="group:cid-1",
                agent_id="agent-x",
                session_id="the-parked-session",
                reply_id="reply-1",
                tool_call_id="call_abc",
                requester_id="",
            ),
        )
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        with self.assertLogs("as", level="WARNING"):
            status = await gw.process(
                ChannelConfirmationResultEvent(
                    channel_id="chan-1",
                    chat_id="group:cid-1",
                    channel_user_id="any-user",
                    approved=True,
                    actor="any-user",
                    approval_id=approval_id,
                ),
            )

        self.assertIs(status, ChannelDecisionStatus.STALE)
        self.assertEqual(
            await bus.queue_drain(MessageBusKeys.wakeup_queue()),
            [],
        )
        self.assertIsNotNone(await load_approval(bus, approval_id))
        self.assertEqual(storage.asked, [])

    async def test_authorization_event_does_not_claim_or_resume(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        status = await gw.process(
            ChannelAuthConfirmationResultEvent(
                channel_id="chan-1",
                chat_id="group:cid-1",
                channel_user_id="requester-1",
                approved=True,
                actor="requester-1",
                approval_id=approval_id,
            ),
        )

        self.assertIs(status, ChannelDecisionStatus.AUTHORIZED)
        self.assertEqual(
            await bus.queue_drain(MessageBusKeys.wakeup_queue()),
            [],
        )
        self.assertIsNotNone(await load_approval(bus, approval_id))
        self.assertEqual(storage.asked, [])

    async def test_enqueue_failure_releases_decision_claim(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        bus.queue_push = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("queue unavailable"),
        )

        with self.assertLogs("as", level="ERROR"):
            status = await gw.process(
                ChannelConfirmationResultEvent(
                    channel_id="chan-1",
                    chat_id="group:cid-1",
                    channel_user_id="requester-1",
                    approved=True,
                    actor="requester-1",
                    approval_id=approval_id,
                ),
            )

        self.assertIs(status, ChannelDecisionStatus.ERROR)
        self.assertFalse(
            await bus.is_locked(
                MessageBusKeys.channel_approval_claim(approval_id),
            ),
        )
        self.assertIsNotNone(await load_approval(bus, approval_id))

    async def test_signal_failure_preserves_submitted_decision(self) -> None:
        for error in (
            RuntimeError("signal unavailable"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                record = _channel_record("user-1")
                storage = _AwaitingStorage(record, "the-parked-session")
                bus = InMemoryMessageBus()
                approval_id = await self._approval(bus)
                gw = ChannelGateway(
                    storage=storage,
                    message_bus=bus,
                    workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
                )
                bus.publish = AsyncMock(  # type: ignore[method-assign]
                    side_effect=error,
                )
                event = ChannelConfirmationResultEvent(
                    channel_id="chan-1",
                    chat_id="group:cid-1",
                    channel_user_id="requester-1",
                    approved=True,
                    actor="requester-1",
                    approval_id=approval_id,
                )
                if isinstance(error, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await gw.process(event)
                else:
                    with self.assertLogs("as", level="ERROR"):
                        status = await gw.process(event)
                    self.assertIs(status, ChannelDecisionStatus.ACCEPTED)

                self.assertTrue(
                    await bus.is_locked(
                        MessageBusKeys.channel_approval_claim(approval_id),
                    ),
                )
                # Even an opposite decision cannot be submitted again.
                status = await gw.process(
                    event.model_copy(update={"approved": False}),
                )
                self.assertIs(status, ChannelDecisionStatus.STALE)
                queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
                self.assertEqual(len(queued), 1)
                self.assertTrue(
                    queued[0][1]["input"]["confirm_results"][0]["confirmed"],
                )

    async def test_cancellation_releases_decision_claim(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        enqueue_started = asyncio.Event()
        keep_enqueue_blocked = asyncio.Event()

        async def blocked_enqueue(*_args: Any, **_kwargs: Any) -> str:
            enqueue_started.set()
            await keep_enqueue_blocked.wait()
            return "never-reached"

        bus.queue_push = AsyncMock(  # type: ignore[method-assign]
            side_effect=blocked_enqueue,
        )
        task = asyncio.create_task(
            gw.process(
                ChannelConfirmationResultEvent(
                    channel_id="chan-1",
                    chat_id="group:cid-1",
                    channel_user_id="requester-1",
                    approved=True,
                    actor="requester-1",
                    approval_id=approval_id,
                ),
            ),
        )
        await asyncio.wait_for(enqueue_started.wait(), timeout=1.0)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(
            await bus.is_locked(
                MessageBusKeys.channel_approval_claim(approval_id),
            ),
        )
        self.assertIsNotNone(await load_approval(bus, approval_id))

    async def test_concurrent_double_click_enqueues_exactly_once(self) -> None:
        record = _channel_record("user-1")
        storage = _AwaitingStorage(record, "the-parked-session")
        bus = InMemoryMessageBus()
        approval_id = await self._approval(bus)
        gw = ChannelGateway(
            storage=storage,
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )
        event = ChannelConfirmationResultEvent(
            channel_id="chan-1",
            chat_id="group:cid-1",
            channel_user_id="requester-1",
            approved=True,
            actor="requester-1",
            approval_id=approval_id,
        )

        statuses = await asyncio.gather(gw.process(event), gw.process(event))

        self.assertCountEqual(
            statuses,
            [ChannelDecisionStatus.ACCEPTED, ChannelDecisionStatus.STALE],
        )
        queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(len(queued), 1)

    async def test_missing_opaque_id_fails_closed(self) -> None:
        record = _channel_record("user-1")
        bus = InMemoryMessageBus()
        gw = ChannelGateway(
            storage=_AwaitingStorage(record, "the-parked-session"),
            message_bus=bus,
            workspace_manager=_WM(isolation=IsolationPolicy.PER_AGENT),
        )

        status = await gw.process(
            ChannelConfirmationResultEvent(
                channel_id="chan-1",
                chat_id="group:cid-1",
                channel_user_id="requester-1",
                approved=True,
                actor="requester-1",
            ),
        )

        self.assertIs(status, ChannelDecisionStatus.STALE)
        self.assertEqual(
            await bus.queue_drain(MessageBusKeys.wakeup_queue()),
            [],
        )
