# -*- coding: utf-8 -*-
"""Tests for browser WebRTC audio and control transport."""

import asyncio
import json
import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from fractions import Fraction
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import numpy as np
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame as AVAudioFrame

from agentscope.app._service import get_realtime_model
from agentscope.app._service._webrtc_audio_transport import (
    WebRTCAudioTransport,
)
from agentscope.app._service._webrtc_session import WebRTCSession
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage import CredentialRecord, RealtimeModelConfig
from agentscope.event import (
    ReplyEndEvent,
    RequireUserConfirmEvent,
    ToolResultEndEvent,
)
from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.realtime import AudioFrame, DashScopeAudioRealtimeModel


class _FakeDataChannel:
    """Minimal event-emitter-compatible RTCDataChannel double."""

    def __init__(self) -> None:
        self.readyState = "open"
        self.sent: list[dict] = []
        self.handlers: dict[str, Callable[..., object]] = {}

    def on(
        self,
        event: str,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Register one event callback."""

        def _register(
            callback: Callable[..., object],
        ) -> Callable[..., object]:
            self.handlers[event] = callback
            return callback

        return _register

    def send(self, raw: str) -> None:
        """Decode and retain one outbound JSON message."""
        self.sent.append(json.loads(raw))

    def emit_message(self, payload: dict) -> None:
        """Deliver one inbound JSON message."""
        callback = self.handlers["message"]
        callback(json.dumps(payload))


class _OneFrameAudioTrack(MediaStreamTrack):
    """Yield one browser-format audio frame, then end."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._sent = False

    async def recv(self) -> AVAudioFrame:
        """Return one 48 kHz mono frame."""
        if self._sent:
            raise MediaStreamError
        self._sent = True
        samples = np.full((1, 960), 1_000, dtype=np.int16)
        frame = AVAudioFrame.from_ndarray(
            samples,
            format="s16",
            layout="mono",
        )
        frame.sample_rate = 48_000
        frame.pts = 0
        frame.time_base = Fraction(1, 48_000)
        return frame


class _FakeAgent:
    """Finite-event realtime agent double."""

    def __init__(self, message: object, event: Any | list[Any]) -> None:
        self.state = SimpleNamespace(context=[message])
        self.events = event if isinstance(event, list) else [event]

    async def __aenter__(self) -> "_FakeAgent":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def reply_stream(
        self,
        transport: object,
    ) -> AsyncIterator[Any]:
        """Yield one event and complete."""
        del transport
        for event in self.events:
            yield event


class _FakeTransport:
    """Idempotently closing transport double."""

    def __init__(self) -> None:
        self.closed = False
        self.errors: list[str] = []

    async def __aenter__(self) -> "_FakeTransport":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Mark the transport closed."""
        self.closed = True

    def send_error(self, detail: str) -> None:
        """Record a session error."""
        self.errors.append(detail)


class _FakePeerConnection:
    """Peer connection close double."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        """Mark the connection closed."""
        self.closed = True


class _FakeStorage:
    """Record the complete persistence call sequence."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record one message write."""
        self.calls.append(
            {
                "method": "upsert_message",
                "user_id": user_id,
                "session_id": session_id,
                "message": message,
            },
        )

    async def update_session_state(self, **kwargs: object) -> None:
        """Record the state snapshot write."""
        self.calls.append({"method": "update_session_state", **kwargs})


class _FakeMessageBus:
    """Record lock, replay-log, and publication operations."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int,
    ) -> AsyncIterator[None]:
        """Record the held lock around the runner."""
        self.calls.append(
            {"method": "acquire_lock", "key": key, "ttl_secs": ttl_secs},
        )
        yield

    async def log_append(
        self,
        key: str,
        event: dict,
        *,
        max_len: int,
    ) -> str:
        """Record an SSE replay event."""
        self.calls.append(
            {
                "method": "log_append",
                "key": key,
                "event": event,
                "max_len": max_len,
            },
        )
        return "1-0"

    async def publish(self, key: str, event: dict) -> None:
        """Record one live SSE publication."""
        self.calls.append({"method": "publish", "key": key, "event": event})

    async def log_trim(self, key: str) -> None:
        """Record replay-log cleanup."""
        self.calls.append({"method": "log_trim", "key": key})


class _BlockedMessageBus(_FakeMessageBus):
    """Keep a session waiting before it can acquire the lock."""

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int,
    ) -> AsyncIterator[None]:
        """Wait forever unless the session task is cancelled."""
        self.calls.append(
            {"method": "acquire_lock", "key": key, "ttl_secs": ttl_secs},
        )
        await asyncio.Event().wait()
        yield


class WebRTCAudioTransportTest(unittest.IsolatedAsyncioTestCase):
    """Verify complete control structures and decoded media."""

    async def asyncSetUp(self) -> None:
        self.channel = _FakeDataChannel()
        self.transport = WebRTCAudioTransport(
            input_sample_rate=16_000,
            output_sample_rate=24_000,
        )
        self.transport.set_data_channel(  # type: ignore[arg-type]
            self.channel,
        )
        await self.transport.start()

    async def asyncTearDown(self) -> None:
        await self.transport.close()

    async def test_audio_control_and_playout_protocol(self) -> None:
        """Move media and full control payloads in both directions."""
        pcm = np.full((2_400,), 2_000, dtype="<i2").tobytes()
        await self.transport.send_audio(pcm, "item-1")
        self.assertEqual(
            self.channel.sent,
            [
                {
                    "type": "audio_duration",
                    "item_id": "item-1",
                    "duration_ms": 100,
                },
            ],
        )

        output = await self.transport.output_track.recv()
        self.assertEqual(
            {
                "sample_rate": output.sample_rate,
                "samples": output.samples,
                "pts": output.pts,
                "time_base": output.time_base,
                "has_audio": bool(np.any(output.to_ndarray())),
            },
            {
                "sample_rate": 48_000,
                "samples": 960,
                "pts": 0,
                "time_base": Fraction(1, 48_000),
                "has_audio": True,
            },
        )
        self.assertEqual(
            self.channel.sent,
            [
                {
                    "type": "audio_duration",
                    "item_id": "item-1",
                    "duration_ms": 100,
                },
                {
                    "type": "audio_start",
                    "item_id": "item-1",
                    "track_time_ms": 0,
                },
            ],
        )

        self.transport.set_input_track(_OneFrameAudioTrack())
        incoming = self.transport.incoming()
        audio = await anext(incoming)
        self.assertIsInstance(audio, AudioFrame)
        self.assertEqual(
            {
                "byte_length": len(audio.pcm),
                "has_audio": bool(np.any(np.frombuffer(audio.pcm, "<i2"))),
            },
            {"byte_length": 608, "has_audio": True},
        )

        self.channel.emit_message(
            {
                "type": "control",
                "control": "interrupt",
                "data": {},
            },
        )
        control = await anext(incoming)
        self.assertEqual(
            control.model_dump(mode="json"),
            {"type": "interrupt", "data": {}},
        )

        confirm_data = {
            "type": "USER_CONFIRM_RESULT",
            "id": "confirm-event-1",
            "created_at": "2026-01-01T00:00:00",
            "reply_id": "reply-1",
            "confirm_results": [
                {
                    "confirmed": True,
                    "tool_call": {
                        "type": "tool_call",
                        "id": "call-1",
                        "name": "Read",
                        "input": '{"path":"README.md"}',
                        "state": "asking",
                        "suggested_rules": [],
                        "created_at": "2026-01-01T00:00:00",
                        "finished_at": None,
                    },
                    "rules": None,
                },
            ],
        }
        self.channel.emit_message(
            {
                "type": "control",
                "control": "user_confirm",
                "data": confirm_data,
            },
        )
        confirmation = await anext(incoming)
        self.assertEqual(
            confirmation.model_dump(mode="json"),
            {"type": "user_confirm", "data": confirm_data},
        )

        clear_task = asyncio.create_task(self.transport.clear_audio())
        while self.channel.sent[-1].get("type") != "clear_audio":
            await asyncio.sleep(0)
        clear_request = self.channel.sent[-1]
        self.assertEqual(
            {
                "keys": set(clear_request),
                "type": clear_request["type"],
                "resume_track_time_ms": clear_request["resume_track_time_ms"],
            },
            {
                "keys": {
                    "type",
                    "request_id",
                    "resume_track_time_ms",
                },
                "type": "clear_audio",
                "resume_track_time_ms": 20,
            },
        )
        self.channel.emit_message(
            {
                "type": "playout_cleared",
                "request_id": clear_request["request_id"],
                "item_id": "",
                "played_ms": 125,
            },
        )
        position = await clear_task
        self.assertEqual(
            self.transport.playout().model_dump(),
            {
                "item_id": "",
                "played_ms": 0,
                "first_played_at": None,
            },
        )

        sent_after_clear = len(self.channel.sent)
        await self.transport.send_audio(pcm, "item-1")
        self.assertEqual(len(self.channel.sent), sent_after_clear)
        await self.transport.send_audio(pcm, "item-2")
        self.assertEqual(
            self.channel.sent[-1],
            {
                "type": "audio_duration",
                "item_id": "item-2",
                "duration_ms": 100,
            },
        )
        self.assertEqual(
            {
                "item_id": position.item_id,
                "played_ms": position.played_ms,
                "has_first_played_at": position.first_played_at is not None,
            },
            {
                "item_id": "item-1",
                "played_ms": 125,
                "has_first_played_at": True,
            },
        )

    async def test_late_clear_ack_does_not_reset_the_next_item(self) -> None:
        """Ignore a timed-out clear acknowledgement from an old item."""
        pcm = np.full((2_400,), 2_000, dtype="<i2").tobytes()
        await self.transport.send_audio(pcm, "item-1")
        await self.transport.output_track.recv()

        with patch(
            "agentscope.app._service._webrtc_audio_transport."
            "_CLEAR_TIMEOUT_SECONDS",
            0,
        ):
            cleared = await self.transport.clear_audio()
        clear_request = self.channel.sent[-1]

        await self.transport.send_audio(pcm, "item-2")
        await self.transport.output_track.recv()
        before_late_ack = self.transport.playout()
        self.channel.emit_message(
            {
                "type": "playout_cleared",
                "request_id": clear_request["request_id"],
                "item_id": "item-1",
                "played_ms": 20,
            },
        )
        await asyncio.sleep(0)

        self.assertEqual(
            {
                "cleared": cleared.model_dump(),
                "before_late_ack": before_late_ack.model_dump(),
                "after_late_ack": self.transport.playout().model_dump(),
            },
            {
                "cleared": {
                    "item_id": "item-1",
                    "played_ms": 0,
                    "first_played_at": None,
                },
                "before_late_ack": {
                    "item_id": "item-2",
                    "played_ms": 0,
                    "first_played_at": None,
                },
                "after_late_ack": {
                    "item_id": "item-2",
                    "played_ms": 0,
                    "first_played_at": None,
                },
            },
        )

    async def test_realtime_config_resolves_the_matching_adapter(self) -> None:
        """The persisted adapter type selects the intended model class."""
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            user_id="alice",
            data={
                "type": "dashscope_credential",
                "id": "credential-1",
                "name": "DashScope",
                "api_key": "secret",
            },
        )
        model = await get_realtime_model(
            "alice",
            RealtimeModelConfig(
                type="dashscope_audio_realtime",
                credential_id="credential-1",
                model="qwen-audio-3.0-realtime-flash",
                parameters={"voice": "longxiaochun"},
            ),
            access,
        )

        self.assertIsInstance(model, DashScopeAudioRealtimeModel)
        self.assertEqual(
            {
                "type": model.type,
                "name": model.model,
                "input_sample_rate": model.input_sample_rate,
                "output_sample_rate": model.output_sample_rate,
                "parameters": model.parameters.model_dump(),
            },
            {
                "type": "dashscope_audio_realtime",
                "name": "qwen-audio-3.0-realtime-flash",
                "input_sample_rate": 16_000,
                "output_sample_rate": 24_000,
                "parameters": {
                    "voice": "longxiaochun",
                    "turn_detection": "server_vad",
                    "vad_threshold": 0.5,
                    "vad_silence_duration_ms": 800,
                    "voiceprint_audio_urls": [],
                    "max_history_turns": 20,
                },
            },
        )
        access.resolve_credential.assert_awaited_once_with(
            "alice",
            "credential-1",
        )


class WebRTCSessionTest(unittest.IsolatedAsyncioTestCase):
    """Verify that a WebRTC run keeps the normal session contract."""

    async def test_run_publishes_persists_and_closes(self) -> None:
        """Persist full agent state after publishing events to SSE."""
        message = {"id": "message-1", "role": "assistant"}
        event = ReplyEndEvent(
            id="event-1",
            created_at="2026-01-01T00:00:00",
            session_id="session-1",
            reply_id="message-1",
        )
        agent = _FakeAgent(message, event)
        transport = _FakeTransport()
        peer_connection = _FakePeerConnection()
        storage = _FakeStorage()
        message_bus = _FakeMessageBus()
        closed = asyncio.Event()
        closed_sessions: list[WebRTCSession] = []

        async def _create_agent() -> _FakeAgent:
            message_bus.calls.append({"method": "agent_factory"})
            return agent

        def _on_closed(session: WebRTCSession) -> None:
            closed_sessions.append(session)
            closed.set()

        session = WebRTCSession(
            connection_id="connection-1",
            peer_connection=peer_connection,  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
            agent_factory=_create_agent,  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            user_id="alice",
            agent_id="agent-1",
            session_id="session-1",
            on_closed=_on_closed,
        )
        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)
        await session.close()

        events_key = MessageBusKeys.session_events("session-1")
        self.assertEqual(
            message_bus.calls,
            [
                {
                    "method": "acquire_lock",
                    "key": MessageBusKeys.session_lock("session-1"),
                    "ttl_secs": MessageBusKeys.SESSION_RUN_TTL_SECS,
                },
                {"method": "agent_factory"},
                {
                    "method": "log_append",
                    "key": events_key,
                    "event": event.model_dump(mode="json"),
                    "max_len": MessageBusKeys.SESSION_REPLAY_MAX_LEN,
                },
                {
                    "method": "publish",
                    "key": events_key,
                    "event": {
                        **event.model_dump(mode="json"),
                        "_entry_id": "1-0",
                    },
                },
                {"method": "log_trim", "key": events_key},
            ],
        )
        self.assertEqual(
            storage.calls,
            [
                {
                    "method": "upsert_message",
                    "user_id": "alice",
                    "session_id": "session-1",
                    "message": message,
                },
                {
                    "method": "update_session_state",
                    "user_id": "alice",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "state": agent.state,
                },
                {
                    "method": "upsert_message",
                    "user_id": "alice",
                    "session_id": "session-1",
                    "message": message,
                },
                {
                    "method": "update_session_state",
                    "user_id": "alice",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "state": agent.state,
                },
            ],
        )
        self.assertEqual(
            {
                "transport_closed": transport.closed,
                "peer_connection_closed": peer_connection.closed,
                "transport_errors": transport.errors,
                "closed_sessions": closed_sessions,
            },
            {
                "transport_closed": True,
                "peer_connection_closed": True,
                "transport_errors": [],
                "closed_sessions": [session],
            },
        )

    async def test_close_cancels_a_runner_waiting_for_the_lock(self) -> None:
        """A losing concurrent offer closes without waiting indefinitely."""
        transport = _FakeTransport()
        peer_connection = _FakePeerConnection()
        storage = _FakeStorage()
        message_bus = _BlockedMessageBus()
        closed_sessions: list[WebRTCSession] = []

        async def _create_agent() -> _FakeAgent:
            raise AssertionError("The agent must not load without the lock.")

        session = WebRTCSession(
            connection_id="connection-1",
            peer_connection=peer_connection,  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
            agent_factory=_create_agent,  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            user_id="alice",
            agent_id="agent-1",
            session_id="session-1",
            on_closed=closed_sessions.append,
        )
        session.start()

        acquired = await session.wait_until_lock_acquired(0.01)
        await session.close()

        self.assertEqual(
            {
                "acquired": acquired,
                "message_bus_calls": message_bus.calls,
                "storage_calls": storage.calls,
                "transport_closed": transport.closed,
                "peer_connection_closed": peer_connection.closed,
                "closed_sessions": closed_sessions,
            },
            {
                "acquired": False,
                "message_bus_calls": [
                    {
                        "method": "acquire_lock",
                        "key": MessageBusKeys.session_lock("session-1"),
                        "ttl_secs": MessageBusKeys.SESSION_RUN_TTL_SECS,
                    },
                ],
                "storage_calls": [],
                "transport_closed": True,
                "peer_connection_closed": True,
                "closed_sessions": [session],
            },
        )

    async def test_tool_boundaries_checkpoint_state(self) -> None:
        """Persist before confirmation waits and after tool completion."""
        message = {"id": "message-1", "role": "assistant"}
        tool_call = ToolCallBlock(
            id="call-1",
            name="Read",
            input='{"path":"README.md"}',
        )
        events = [
            RequireUserConfirmEvent(
                id="event-1",
                created_at="2026-01-01T00:00:00",
                reply_id="message-1",
                tool_calls=[tool_call],
            ),
            ToolResultEndEvent(
                id="event-2",
                created_at="2026-01-01T00:00:01",
                reply_id="message-1",
                tool_call_id="call-1",
                state=ToolResultState.SUCCESS,
            ),
        ]
        agent = _FakeAgent(message, events)
        transport = _FakeTransport()
        peer_connection = _FakePeerConnection()
        storage = _FakeStorage()
        message_bus = _FakeMessageBus()
        closed = asyncio.Event()

        async def _create_agent() -> _FakeAgent:
            return agent

        session = WebRTCSession(
            connection_id="connection-1",
            peer_connection=peer_connection,  # type: ignore[arg-type]
            transport=transport,  # type: ignore[arg-type]
            agent_factory=_create_agent,  # type: ignore[arg-type]
            storage=storage,  # type: ignore[arg-type]
            message_bus=message_bus,  # type: ignore[arg-type]
            user_id="alice",
            agent_id="agent-1",
            session_id="session-1",
            on_closed=lambda _: closed.set(),
        )
        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)

        expected_writes = [
            {
                "method": "upsert_message",
                "user_id": "alice",
                "session_id": "session-1",
                "message": message,
            },
            {
                "method": "update_session_state",
                "user_id": "alice",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "state": agent.state,
            },
        ]
        self.assertEqual(
            {
                "storage_calls": storage.calls,
                "published_events": [
                    call["event"]
                    for call in message_bus.calls
                    if call["method"] == "publish"
                ],
            },
            {
                "storage_calls": expected_writes * 3,
                "published_events": [
                    {
                        **event.model_dump(mode="json"),
                        "_entry_id": "1-0",
                    }
                    for event in events
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
