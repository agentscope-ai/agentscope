# -*- coding: utf-8 -*-
"""Lifecycle owner for one browser WebRTC realtime session."""

import asyncio
from collections.abc import Awaitable, Callable

from aiortc import RTCPeerConnection

from ..._logging import logger
from ...agent import RealtimeAgent
from ...event import (
    ReplyEndEvent,
    RequireUserConfirmEvent,
    TextBlockEndEvent,
    ToolResultEndEvent,
)
from .._bus_ops import publish_session_event
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from ._webrtc_audio_transport import WebRTCAudioTransport


class WebRTCSession:
    """Run and persist one realtime agent over a peer connection."""

    def __init__(
        self,
        *,
        connection_id: str,
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
        self.connection_id = connection_id
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
        self._close_lock = asyncio.Lock()
        self._closing = False
        self._lock_acquired = asyncio.Event()

    def start(self) -> None:
        """Start the agent pump after WebRTC negotiation succeeds."""
        if self._task is not None:
            raise RuntimeError("The WebRTC session is already running.")
        self._task = asyncio.create_task(
            self._run(),
            name=f"webrtc-session-{self.session_id}",
        )

    async def close(self) -> None:
        """End the transport and wait for persistence to finish."""
        async with self._close_lock:
            if not self._closing:
                self._closing = True
                await self.transport.close()

        task = self._task
        if task is not None and task is not asyncio.current_task():
            if not self._lock_acquired.is_set():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        elif task is None:
            await self.peer_connection.close()
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
        events_key = MessageBusKeys.session_events(self.session_id)
        try:
            async with self.message_bus.acquire_lock(
                MessageBusKeys.session_lock(self.session_id),
                ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
            ):
                self._lock_acquired.set()
                self.agent = await self._agent_factory()
                async with self.transport:
                    async with self.agent:
                        async for event in self.agent.reply_stream(
                            self.transport,
                        ):
                            await publish_session_event(
                                self.message_bus,
                                self.session_id,
                                event.model_dump(mode="json"),
                            )
                            if isinstance(
                                event,
                                (
                                    ReplyEndEvent,
                                    RequireUserConfirmEvent,
                                    TextBlockEndEvent,
                                    ToolResultEndEvent,
                                ),
                            ):
                                try:
                                    await self._persist_state()
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to checkpoint WebRTC "
                                        "session %r: %s",
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
            if self._lock_acquired.is_set():
                try:
                    await self._persist_state()
                    await self.message_bus.log_trim(events_key)
                except Exception as exc:
                    logger.exception(
                        "Failed to persist WebRTC session %r: %s",
                        self.session_id,
                        exc,
                    )
            await self.transport.close()
            await self.peer_connection.close()
            self._on_closed(self)

    async def _persist_state(self) -> None:
        """Persist the latest complete messages and agent state."""
        if self.agent is None:
            return
        for message in self.agent.state.context:
            await self.storage.upsert_message(
                self.user_id,
                self.session_id,
                message,
            )
        await self.storage.update_session_state(
            user_id=self.user_id,
            agent_id=self.agent_id,
            session_id=self.session_id,
            state=self.agent.state,
        )
