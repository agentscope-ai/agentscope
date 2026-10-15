# -*- coding: utf-8 -*-
"""Lifecycle owner for one browser WebRTC realtime session."""

import asyncio
from collections.abc import Awaitable, Callable

from aiortc import RTCPeerConnection

from ..._logging import logger
from ...agent import RealtimeAgent
from ...event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
)
from ...state import AgentState
from .._bus_ops import publish_session_event
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from ._webrtc_audio_transport import WebRTCAudioTransport

_CHECKPOINT_LOCK_TTL_SECS = (
    MessageBusKeys.SESSION_EVENT_CHECKPOINT_LOCK_TTL_SECS
)


class WebRTCSession:
    """Run and persist one realtime agent over a peer connection."""

    def __init__(
        self,
        *,
        peer_connection: RTCPeerConnection,
        transport: WebRTCAudioTransport,
        agent_factory: Callable[[], Awaitable[RealtimeAgent]],
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        agent_id: str,
        session_id: str,
        on_closed: Callable[["WebRTCSession"], None],
    ) -> None:
        self.peer_connection = peer_connection
        self.transport = transport
        self._agent_factory = agent_factory
        self.agent: RealtimeAgent | None = None
        self.storage = storage
        self.message_bus = message_bus
        self.user_id = user_id
        self.agent_id = agent_id
        self.session_id = session_id
        self._on_closed = on_closed
        self._task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self._startup_complete = False
        self._lock_acquired = asyncio.Event()
        self._persisted_messages: dict[str, str] = {}
        self._open_user_replies: set[str] = set()
        self._pcm_block_ids: set[str] = set()
        self._last_entry_id: str | None = None
        self._checkpoint_entry_id: str | None = None

    def start(self) -> None:
        """Start the agent pump after WebRTC negotiation succeeds."""
        if self._task is not None or self._closing:
            raise RuntimeError("The WebRTC session is running or closed.")
        self._task = asyncio.create_task(
            self._run(),
            name=f"webrtc-session-{self.session_id}",
        )

    def request_close(self) -> None:
        """Schedule one owned close task from a synchronous callback."""
        if self._closing or self._close_task is not None:
            return
        self._close_task = asyncio.create_task(
            self.close(),
            name=f"webrtc-session-close-{self.session_id}",
        )
        self._close_task.add_done_callback(self._on_close_done)

    def _on_close_done(self, task: asyncio.Task[None]) -> None:
        """Retrieve an asynchronous close error instead of losing it."""
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Failed to close WebRTC session %r: %s",
                self.session_id,
                error,
            )

    async def close(self) -> None:
        """End the transport and wait for persistence to finish."""
        task = self._task
        async with self._close_lock:
            if not self._closing:
                self._closing = True
                await self.transport.close()
                if (
                    task is not None
                    and task is not asyncio.current_task()
                    and not self._startup_complete
                ):
                    task.cancel()

        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)
        # A task cancelled before its first step never enters _run's finally.
        await self._close_resources()

    async def _close_resources(self) -> None:
        """Close the peer and retire this session exactly once."""
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            await self.transport.close()
            await self.peer_connection.close()
            self._closed = True
            self._on_closed(self)

    async def wait_until_lock_acquired(self, timeout: float) -> bool:
        """Wait until this runner owns the distributed session lock."""
        try:
            await asyncio.wait_for(
                self._lock_acquired.wait(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return False
        return True

    async def _run(self) -> None:
        """Hold the session lock while forwarding events to the SSE log."""
        try:
            async with self.message_bus.acquire_lock(
                MessageBusKeys.session_lock(self.session_id),
                ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
            ):
                self._lock_acquired.set()
                try:
                    self.agent = await self._agent_factory()
                    self._persisted_messages = {
                        message.id: message.model_dump_json()
                        for message in self.agent.state.context
                    }
                    async with self.transport:
                        async with self.agent:
                            self._startup_complete = True
                            async for event in self.agent.reply_stream(
                                self.transport,
                            ):
                                if isinstance(event, DataBlockStartEvent) and (
                                    event.media_type.startswith("audio/pcm")
                                ):
                                    self._pcm_block_ids.add(event.block_id)
                                    continue
                                if isinstance(
                                    event,
                                    DataBlockDeltaEvent,
                                ) and event.media_type.startswith("audio/pcm"):
                                    self._pcm_block_ids.add(event.block_id)
                                    continue
                                if (
                                    isinstance(event, DataBlockEndEvent)
                                    and event.block_id in self._pcm_block_ids
                                ):
                                    self._pcm_block_ids.discard(event.block_id)
                                    continue
                                checkpoint = self.agent.checkpoint_snapshot(
                                    event,
                                )
                                payload = event.model_dump(mode="json")
                                entry_id = await publish_session_event(
                                    self.message_bus,
                                    self.session_id,
                                    payload,
                                )
                                self._last_entry_id = entry_id
                                if (
                                    isinstance(event, ReplyStartEvent)
                                    and event.role == "user"
                                ):
                                    self._open_user_replies.add(
                                        event.reply_id,
                                    )
                                elif isinstance(event, ReplyEndEvent):
                                    self._open_user_replies.discard(
                                        event.reply_id,
                                    )
                                if (
                                    checkpoint is not None
                                    and not self._open_user_replies
                                ):
                                    try:
                                        await self._checkpoint_state(
                                            entry_id,
                                            checkpoint,
                                        )
                                    except Exception as exc:
                                        logger.warning(
                                            "Failed to checkpoint WebRTC "
                                            "session %r: %s",
                                            self.session_id,
                                            exc,
                                        )
                finally:
                    try:
                        async with self.message_bus.acquire_lock(
                            MessageBusKeys.session_event_checkpoint_lock(
                                self.session_id,
                            ),
                            ttl_secs=_CHECKPOINT_LOCK_TTL_SECS,
                        ):
                            await self._persist_state()
                            if (
                                self._last_entry_id is not None
                                and self._last_entry_id
                                != self._checkpoint_entry_id
                            ):
                                await self._set_checkpoint_cursor(
                                    self._last_entry_id,
                                )
                    except Exception as exc:
                        logger.exception(
                            "Failed to persist WebRTC session %r: %s",
                            self.session_id,
                            exc,
                        )
        except Exception as exc:
            logger.exception(
                "WebRTC session %r failed: %s",
                self.session_id,
                exc,
            )
            self.transport.send_error(str(exc))
        finally:
            async with self._close_lock:
                self._closing = True
            try:
                if self.agent is not None and not self._startup_complete:
                    # __aexit__ is not called when provider setup is cancelled
                    # during the agent's __aenter__.
                    await self.agent.close()
            finally:
                await self._close_resources()

    async def _checkpoint_state(
        self,
        entry_id: str | None,
        state: AgentState,
    ) -> None:
        """Persist state and bind it to the replay entry it includes."""
        async with self.message_bus.acquire_lock(
            MessageBusKeys.session_event_checkpoint_lock(self.session_id),
            ttl_secs=_CHECKPOINT_LOCK_TTL_SECS,
        ):
            await self._persist_state(state)
            if entry_id is not None:
                await self._set_checkpoint_cursor(entry_id)

    async def _set_checkpoint_cursor(self, entry_id: str) -> None:
        """Record the last event included in the persisted state."""
        await self.message_bus.registry_set(
            MessageBusKeys.session_event_checkpoint(self.session_id),
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
            entry_id,
        )
        self._checkpoint_entry_id = entry_id
        await self.message_bus.log_trim(
            MessageBusKeys.session_events(self.session_id),
            before_id=entry_id,
        )

    async def _persist_state(self, state: AgentState | None = None) -> None:
        """Persist the latest complete messages and agent state."""
        if self.agent is None:
            return
        state = state or self.agent.state
        current_messages = {
            message.id: message.model_dump_json() for message in state.context
        }
        for message in state.context:
            serialized = current_messages[message.id]
            if self._persisted_messages.get(message.id) == serialized:
                continue
            await self.storage.upsert_message(
                self.user_id,
                self.session_id,
                message,
            )
            self._persisted_messages[message.id] = serialized
        await self.storage.update_session_state(
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            state=state,
        )
        removed_ids = self._persisted_messages.keys() - current_messages.keys()
        for message_id in sorted(removed_ids):
            await self.storage.delete_message(
                self.user_id,
                self.session_id,
                message_id,
            )
            self._persisted_messages.pop(message_id, None)
