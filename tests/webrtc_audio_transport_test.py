# -*- coding: utf-8 -*-
"""Tests for browser WebRTC audio and control transport."""
# pylint: disable=protected-access

import asyncio
import json
import unittest
from collections.abc import Callable
from contextlib import asynccontextmanager
from fractions import Fraction
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame as AVAudioFrame
from fastapi import HTTPException

from agentscope.app._router._realtime import _create_realtime_offer
from agentscope.app._router._schema import (
    RealtimeOfferRequest,
    RealtimeOfferResponse,
)
from agentscope.app._service import get_realtime_model
from agentscope.app._service._webrtc_audio_transport import (
    WebRTCAudioTransport,
)
from agentscope.app._service._webrtc_session import WebRTCSession
from agentscope.app.message_bus import MessageBusKeys
from agentscope.app.storage import CredentialRecord, RealtimeModelConfig
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


class _FakeMessage(dict):
    """Dictionary fixture exposing the ``Msg.id`` attribute contract."""

    @property
    def id(self) -> str:
        """Return the fixture's message id."""
        return self["id"]

    def model_dump_json(self) -> str:
        """Return stable JSON for changed-message detection."""
        return json.dumps(self, sort_keys=True)


class _FakeAgent:
    """Finite-event realtime agent double."""

    def __init__(self, message: object, event: Any | list[Any]) -> None:
        context_message = (
            _FakeMessage(message) if isinstance(message, dict) else message
        )
        self.state = SimpleNamespace(context=[context_message])
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

    def checkpoint_snapshot(self, event: Any) -> object | None:
        """Return state for the same boundaries as the real agent."""
        if isinstance(
            event,
            (
                ReplyEndEvent,
                RequireUserConfirmEvent,
                ToolResultEndEvent,
            ),
        ):
            return self.state
        return None


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
        self.messages: dict[str, object] = {}

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record one message write."""
        message_id = (
            message["id"]
            if isinstance(message, dict)
            else getattr(message, "id")
        )
        self.messages[message_id] = message
        self.calls.append(
            {
                "method": "upsert_message",
                "user_id": user_id,
                "session_id": session_id,
                "message": message,
            },
        )

    async def delete_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> bool:
        """Delete and record one persisted message."""
        deleted = self.messages.pop(message_id, None) is not None
        self.calls.append(
            {
                "method": "delete_message",
                "user_id": user_id,
                "session_id": session_id,
                "message_id": message_id,
            },
        )
        return deleted

    async def update_session_state(self, **kwargs: object) -> None:
        """Record the state snapshot write."""
        self.calls.append({"method": "update_session_state", **kwargs})


class _LockAwareStorage(_FakeStorage):
    """Record whether each state write holds the session run lock."""

    def __init__(self, message_bus: "_FakeMessageBus") -> None:
        super().__init__()
        self._message_bus = message_bus
        self.writes_under_session_lock: list[bool] = []

    async def update_session_state(self, **kwargs: object) -> None:
        """Record the lock state alongside the normal write."""
        session_id = str(kwargs["session_id"])
        self.writes_under_session_lock.append(
            MessageBusKeys.session_lock(session_id)
            in self._message_bus.held_locks,
        )
        await super().update_session_state(**kwargs)


class _FakeMessageBus:
    """Record lock, replay-log, and publication operations."""

    def __init__(self, sequence_entries: bool = False) -> None:
        self.calls: list[dict] = []
        self.held_locks: set[str] = set()
        self._sequence_entries = sequence_entries
        self._entry_count = 0

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
        self.held_locks.add(key)
        try:
            yield
        finally:
            self.held_locks.remove(key)

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
        if self._sequence_entries:
            self._entry_count += 1
            return f"{self._entry_count}-0"
        return "1-0"

    async def publish(self, key: str, event: dict) -> None:
        """Record one live SSE publication."""
        self.calls.append({"method": "publish", "key": key, "event": event})

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        """Record replay-log cleanup."""
        self.calls.append(
            {
                "method": "log_trim",
                "key": key,
                "before_id": before_id,
            },
        )

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Record one registry write."""
        self.calls.append(
            {
                "method": "registry_set",
                "namespace": namespace,
                "field": field,
                "value": value,
                "ttl_secs": ttl_secs,
            },
        )

    async def registry_drop(self, namespace: str) -> None:
        """Record registry cleanup."""
        self.calls.append(
            {"method": "registry_drop", "namespace": namespace},
        )


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


def _make_session(
    *,
    agent_factory: Any,
    storage: Any,
    message_bus: Any,
    on_closed: Callable[[WebRTCSession], None],
    transport: Any | None = None,
    peer_connection: Any | None = None,
) -> WebRTCSession:
    """Build a session with the shared test identity."""
    return WebRTCSession(
        peer_connection=peer_connection or _FakePeerConnection(),
        transport=transport or _FakeTransport(),
        agent_factory=agent_factory,
        storage=storage,
        message_bus=message_bus,
        user_id="alice",
        agent_id="agent-1",
        session_id="session-1",
        on_closed=on_closed,
    )


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
        self.assertListEqual(
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
        self.assertDictEqual(
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
        self.assertListEqual(
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
        self.assertDictEqual(
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
        self.assertDictEqual(
            control.model_dump(mode="json"),
            {"type": "interrupt", "data": {}},
        )

        confirm_data = {
            "type": "USER_CONFIRM_RESULT",
            "id": "confirm-event-1",
            "created_at": "2026-01-01T00:00:00",
            "reply_id": "reply-1",
            "confirm_results": [],
        }
        self.channel.emit_message(
            {
                "type": "control",
                "control": "user_confirm",
                "data": confirm_data,
            },
        )
        confirmation = await anext(incoming)
        self.assertDictEqual(
            confirmation.model_dump(mode="json"),
            {"type": "user_confirm", "data": confirm_data},
        )

        clear_task = asyncio.create_task(self.transport.clear_audio())
        while self.channel.sent[-1].get("type") != "clear_audio":
            await asyncio.sleep(0)
        clear_request = self.channel.sent[-1]
        self.assertDictEqual(
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
        self.assertDictEqual(
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
        self.assertDictEqual(
            self.channel.sent[-1],
            {
                "type": "audio_duration",
                "item_id": "item-2",
                "duration_ms": 100,
            },
        )
        state_after_item_2 = {
            "sent": self.channel.sent.copy(),
            "audio_bytes": self.transport._audio_bytes.copy(),
            "latest_item_id": self.transport._latest_item_id,
            "blocked_item_id": self.transport._blocked_item_id,
        }
        await self.transport.send_audio(pcm, "item-1")
        self.assertDictEqual(
            {
                "sent": self.channel.sent,
                "audio_bytes": self.transport._audio_bytes,
                "latest_item_id": self.transport._latest_item_id,
                "blocked_item_id": self.transport._blocked_item_id,
            },
            state_after_item_2,
        )
        self.assertDictEqual(
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

    async def test_audio_duration_discards_completed_item_counts(self) -> None:
        """Only the current sequential output item keeps byte counts."""
        pcm = np.full((2_400,), 2_000, dtype="<i2").tobytes()

        await self.transport.send_audio(pcm, "item-1")
        await self.transport.send_audio(pcm, "item-1")
        await self.transport.send_audio(pcm, "item-2")

        self.assertDictEqual(
            {
                "audio_bytes": self.transport._audio_bytes,
                "latest_item_id": self.transport._latest_item_id,
                "sent": self.channel.sent,
            },
            {
                "audio_bytes": {"item-2": len(pcm)},
                "latest_item_id": "item-2",
                "sent": [
                    {
                        "type": "audio_duration",
                        "item_id": "item-1",
                        "duration_ms": 100,
                    },
                    {
                        "type": "audio_duration",
                        "item_id": "item-1",
                        "duration_ms": 200,
                    },
                    {
                        "type": "audio_duration",
                        "item_id": "item-2",
                        "duration_ms": 100,
                    },
                ],
            },
        )

    async def test_audio_queue_drops_only_the_oldest_audio(self) -> None:
        """A stalled consumer keeps recent audio and every control frame."""
        self.transport._max_queued_audio_frames = 2
        self.transport._enqueue_incoming(AudioFrame(pcm=b"first"))
        self.transport._enqueue_incoming(AudioFrame(pcm=b"second"))
        self.channel.emit_message(
            {
                "type": "control",
                "control": "interrupt",
                "data": {},
            },
        )
        self.transport._enqueue_incoming(AudioFrame(pcm=b"third"))

        incoming = self.transport.incoming()
        frames = [await anext(incoming) for _ in range(3)]
        await incoming.aclose()

        self.assertListEqual(
            [
                (
                    {"type": "audio", "pcm": frame.pcm}
                    if isinstance(frame, AudioFrame)
                    else frame.model_dump(mode="json")
                )
                for frame in frames
            ],
            [
                {"type": "audio", "pcm": b"second"},
                {"type": "interrupt", "data": {}},
                {"type": "audio", "pcm": b"third"},
            ],
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

        self.assertDictEqual(
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
        self.assertDictEqual(
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


class RealtimeOfferTest(unittest.IsolatedAsyncioTestCase):
    """Verify browser offer validation and resource ownership."""

    def setUp(self) -> None:
        self.config = SimpleNamespace(
            parameters={"turn_detection": "server_vad"},
        )
        self.session = SimpleNamespace(
            config=SimpleNamespace(realtime_model_config=self.config),
        )
        self.storage = SimpleNamespace(
            get_session=AsyncMock(return_value=self.session),
        )
        self.access = SimpleNamespace(resolve_agent=AsyncMock())
        self.connections: dict[tuple[str, str], object] = {}
        self.request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    realtime_connections=self.connections,
                    realtime_ice_servers=[],
                ),
            ),
        )
        self.message_bus = SimpleNamespace(
            is_locked=AsyncMock(return_value=False),
        )
        self.realtime_service = SimpleNamespace(create_agent=AsyncMock())
        self.model = SimpleNamespace(
            input_sample_rate=16_000,
            output_sample_rate=24_000,
        )
        self.peer = MagicMock()
        self.peer.connectionState = "new"
        self.peer.on.side_effect = lambda _: lambda callback: callback
        self.peer.setRemoteDescription = AsyncMock()
        self.peer.createAnswer = AsyncMock(
            return_value=SimpleNamespace(sdp="answer", type="answer"),
        )
        self.peer.setLocalDescription = AsyncMock()
        self.peer.localDescription = SimpleNamespace(
            sdp="answer",
            type="answer",
        )
        self.peer.close = AsyncMock()
        self.transport = MagicMock()
        self.transport.output_track = object()
        self.transport.close = AsyncMock()
        self.runner = MagicMock()
        self.runner.wait_until_lock_acquired = AsyncMock(return_value=True)
        self.runner.close = AsyncMock()

        def _create_runner(**kwargs: object) -> MagicMock:
            on_closed = cast(
                Callable[[object], None],
                kwargs["on_closed"],
            )

            async def _close() -> None:
                on_closed(self.runner)

            self.runner.close.side_effect = _close
            return self.runner

        self.runner_factory = MagicMock(side_effect=_create_runner)
        self.patchers = [
            patch(
                "agentscope.app._router._realtime.get_realtime_model",
                new=AsyncMock(return_value=self.model),
            ),
            patch("aiortc.RTCPeerConnection", return_value=self.peer),
            patch(
                "agentscope.app._service._webrtc_audio_transport."
                "WebRTCAudioTransport",
                return_value=self.transport,
            ),
            patch(
                "agentscope.app._service._webrtc_session.WebRTCSession",
                self.runner_factory,
            ),
        ]
        self.get_model = self.patchers[0].start()
        for patcher in self.patchers[1:]:
            patcher.start()

        def _stop_patchers() -> None:
            for patcher in reversed(self.patchers):
                patcher.stop()

        self.addCleanup(_stop_patchers)

    async def _offer(self) -> RealtimeOfferResponse:
        """Create an offer using the shared browser fixtures."""
        return await _create_realtime_offer(
            session_id="session-1",
            body=RealtimeOfferRequest(agent_id="agent-1", sdp="v=0"),
            request=self.request,
            user_id="alice",
            storage=self.storage,
            message_bus=self.message_bus,
            access=self.access,
            realtime_service=self.realtime_service,
        )

    async def test_browser_rejects_manual_turn_detection(self) -> None:
        """Browser voice requires provider-owned turn detection."""
        self.config.parameters["turn_detection"] = "none"

        with self.assertRaises(HTTPException) as raised:
            await self._offer()

        self.assertDictEqual(
            {
                "status_code": raised.exception.status_code,
                "detail": raised.exception.detail,
                "model_calls": self.get_model.await_count,
            },
            {
                "status_code": 409,
                "detail": (
                    "Browser voice mode does not support "
                    "turn_detection='none'. Select provider turn detection."
                ),
                "model_calls": 0,
            },
        )

    async def test_offer_replaces_the_previous_connection(self) -> None:
        """A reconnect closes the previous local runner before reload."""
        previous = SimpleNamespace(close=AsyncMock())
        self.connections[("alice", "session-1")] = previous

        response = await self._offer()

        self.assertDictEqual(
            {
                "response": response.model_dump(),
                "session_loads": self.storage.get_session.await_count,
                "connection_is_new": (
                    self.connections[("alice", "session-1")] is self.runner
                ),
                "runner_started": self.runner.start.call_count,
            },
            {
                "response": {"sdp": "answer", "type": "answer"},
                "session_loads": 2,
                "connection_is_new": True,
                "runner_started": 1,
            },
        )
        previous.close.assert_awaited_once_with()

    async def test_negotiation_failure_closes_created_resources(self) -> None:
        """A failed SDP negotiation closes both created resources."""
        self.peer.createAnswer.side_effect = RuntimeError("bad offer")

        with self.assertRaises(HTTPException) as raised:
            await self._offer()

        self.assertDictEqual(
            {
                "status_code": raised.exception.status_code,
                "detail": raised.exception.detail,
                "runner_created": self.runner_factory.call_count,
                "connections": self.connections,
            },
            {
                "status_code": 400,
                "detail": "WebRTC negotiation failed: bad offer",
                "runner_created": 0,
                "connections": {},
            },
        )
        self.transport.close.assert_awaited_once_with()
        self.peer.close.assert_awaited_once_with()

    async def test_lock_timeout_closes_runner_and_resources(self) -> None:
        """A lost session-lock race returns 409 without leaking resources."""
        self.runner.wait_until_lock_acquired.return_value = False

        with self.assertRaises(HTTPException) as raised:
            await self._offer()

        self.assertDictEqual(
            {
                "status_code": raised.exception.status_code,
                "detail": raised.exception.detail,
                "connections": self.connections,
            },
            {
                "status_code": 409,
                "detail": "Session 'session-1' is already running.",
                "connections": {},
            },
        )
        self.runner.close.assert_awaited_once_with()
        self.transport.close.assert_awaited_once_with()
        self.peer.close.assert_awaited_once_with()


class WebRTCSessionTest(unittest.IsolatedAsyncioTestCase):
    """Verify that a WebRTC run keeps the normal session contract."""

    async def test_request_close_owns_and_deduplicates_the_task(self) -> None:
        """Synchronous callbacks share one retained asynchronous close."""
        transport = _FakeTransport()
        peer_connection = _FakePeerConnection()
        storage = _FakeStorage()
        message_bus = _FakeMessageBus()
        closed_sessions: list[WebRTCSession] = []

        async def _create_agent() -> _FakeAgent:
            raise AssertionError(
                "An unstarted session must not load an agent.",
            )

        session = _make_session(
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            on_closed=closed_sessions.append,
        )

        session.request_close()
        close_task = session._close_task
        session.request_close()
        if close_task is None:
            self.fail("request_close() did not retain its task.")
        await close_task

        self.assertDictEqual(
            {
                "same_task": session._close_task is close_task,
                "task_done": close_task.done(),
                "transport_closed": transport.closed,
                "peer_connection_closed": peer_connection.closed,
                "storage_calls": storage.calls,
                "message_bus_calls": message_bus.calls,
                "closed_sessions": closed_sessions,
            },
            {
                "same_task": True,
                "task_done": True,
                "transport_closed": True,
                "peer_connection_closed": True,
                "storage_calls": [],
                "message_bus_calls": [],
                "closed_sessions": [session],
            },
        )

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
        message_bus = _FakeMessageBus()
        storage = _LockAwareStorage(message_bus)
        closed = asyncio.Event()
        closed_sessions: list[WebRTCSession] = []

        async def _create_agent() -> _FakeAgent:
            message_bus.calls.append({"method": "agent_factory"})
            return agent

        def _on_closed(session: WebRTCSession) -> None:
            closed_sessions.append(session)
            closed.set()

        session = _make_session(
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            on_closed=_on_closed,
        )
        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)
        await session.close()

        events_key = MessageBusKeys.session_events("session-1")
        self.assertListEqual(
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
                {
                    "method": "acquire_lock",
                    "key": MessageBusKeys.session_event_checkpoint_lock(
                        "session-1",
                    ),
                    "ttl_secs": (
                        MessageBusKeys.SESSION_EVENT_CHECKPOINT_LOCK_TTL_SECS
                    ),
                },
                {
                    "method": "registry_set",
                    "namespace": (
                        MessageBusKeys.session_event_checkpoint(
                            "session-1",
                        )
                    ),
                    "field": MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                    "value": "1-0",
                    "ttl_secs": None,
                },
                {
                    "method": "log_trim",
                    "key": events_key,
                    "before_id": "1-0",
                },
                {
                    "method": "acquire_lock",
                    "key": MessageBusKeys.session_event_checkpoint_lock(
                        "session-1",
                    ),
                    "ttl_secs": (
                        MessageBusKeys.SESSION_EVENT_CHECKPOINT_LOCK_TTL_SECS
                    ),
                },
            ],
        )
        self.assertListEqual(
            storage.calls,
            [
                {
                    "method": "update_session_state",
                    "user_id": "alice",
                    "agent_id": "agent-1",
                    "session_id": "session-1",
                    "state": agent.state,
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
        self.assertDictEqual(
            {
                "transport_closed": transport.closed,
                "peer_connection_closed": peer_connection.closed,
                "transport_errors": transport.errors,
                "closed_sessions": closed_sessions,
                "writes_under_session_lock": (
                    storage.writes_under_session_lock
                ),
            },
            {
                "transport_closed": True,
                "peer_connection_closed": True,
                "transport_errors": [],
                "closed_sessions": [session],
                "writes_under_session_lock": [True, True],
            },
        )

    async def test_delayed_user_transcript_holds_replay_cursor(self) -> None:
        """Do not skip the user start while its transcript is pending."""
        agent = _FakeAgent(
            {"id": "user-1", "role": "user"},
            [
                ReplyStartEvent(
                    session_id="session-1",
                    reply_id="user-1",
                    name="user",
                    role="user",
                ),
                ReplyEndEvent(
                    session_id="session-1",
                    reply_id="assistant-1",
                ),
                TextBlockStartEvent(
                    reply_id="user-1",
                    block_id="text-1",
                ),
                TextBlockDeltaEvent(
                    reply_id="user-1",
                    block_id="text-1",
                    delta="hello",
                ),
                TextBlockEndEvent(
                    reply_id="user-1",
                    block_id="text-1",
                ),
                ReplyEndEvent(
                    session_id="session-1",
                    reply_id="user-1",
                ),
            ],
        )
        message_bus = _FakeMessageBus(sequence_entries=True)
        storage = _FakeStorage()
        closed = asyncio.Event()
        session = _make_session(
            agent_factory=AsyncMock(return_value=agent),
            storage=storage,
            message_bus=message_bus,
            on_closed=lambda _: closed.set(),
        )

        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)

        self.assertListEqual(
            [
                call["value"]
                for call in message_bus.calls
                if call["method"] == "registry_set"
            ],
            ["6-0"],
        )

    async def test_checkpoint_deletes_message_removed_from_context(
        self,
    ) -> None:
        """A later checkpoint removes a message no longer in context."""
        message = {"id": "reply-1", "role": "assistant"}
        agent = _FakeAgent(
            message,
            ReplyEndEvent(
                session_id="session-1",
                reply_id="reply-1",
            ),
        )
        storage = _FakeStorage()
        session = _make_session(
            agent_factory=AsyncMock(),
            storage=storage,
            message_bus=_FakeMessageBus(),
            on_closed=lambda _: None,
        )
        session.agent = agent  # type: ignore[assignment]

        await session._persist_state()
        agent.state.context.clear()
        await session._persist_state()

        self.assertDictEqual(
            {
                "reloaded_messages": list(storage.messages.values()),
                "storage_calls": storage.calls,
            },
            {
                "reloaded_messages": [],
                "storage_calls": [
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
                        "method": "update_session_state",
                        "user_id": "alice",
                        "agent_id": "agent-1",
                        "session_id": "session-1",
                        "state": agent.state,
                    },
                    {
                        "method": "delete_message",
                        "user_id": "alice",
                        "session_id": "session-1",
                        "message_id": "reply-1",
                    },
                ],
            },
        )

    async def test_pcm_events_are_not_published_to_session_events(
        self,
    ) -> None:
        """WebRTC carries PCM without duplicating it over session SSE."""
        message = {"id": "message-1", "role": "assistant"}
        events = [
            DataBlockStartEvent(
                id="event-1",
                created_at="2026-01-01T00:00:00",
                reply_id="message-1",
                block_id="audio-1",
                media_type="audio/pcm;rate=24000",
            ),
            DataBlockDeltaEvent(
                id="event-2",
                created_at="2026-01-01T00:00:01",
                reply_id="message-1",
                block_id="audio-1",
                data="AQA=",
                media_type="audio/pcm;rate=24000",
            ),
            DataBlockEndEvent(
                id="event-3",
                created_at="2026-01-01T00:00:02",
                reply_id="message-1",
                block_id="audio-1",
            ),
        ]
        agent = _FakeAgent(message, events)
        transport = _FakeTransport()
        peer_connection = _FakePeerConnection()
        storage = _FakeStorage()
        message_bus = _FakeMessageBus()
        closed = asyncio.Event()

        async def _create_agent() -> _FakeAgent:
            message_bus.calls.append({"method": "agent_factory"})
            return agent

        session = _make_session(
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            on_closed=lambda _: closed.set(),
        )
        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)

        self.assertListEqual(
            message_bus.calls,
            [
                {
                    "method": "acquire_lock",
                    "key": MessageBusKeys.session_lock("session-1"),
                    "ttl_secs": MessageBusKeys.SESSION_RUN_TTL_SECS,
                },
                {"method": "agent_factory"},
                {
                    "method": "acquire_lock",
                    "key": MessageBusKeys.session_event_checkpoint_lock(
                        "session-1",
                    ),
                    "ttl_secs": (
                        MessageBusKeys.SESSION_EVENT_CHECKPOINT_LOCK_TTL_SECS
                    ),
                },
            ],
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

        session = _make_session(
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            on_closed=closed_sessions.append,
        )
        session.start()

        acquired = await session.wait_until_lock_acquired(0.01)
        await session.close()

        self.assertDictEqual(
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

    async def test_checkpoint_boundaries_persist_state(self) -> None:
        """Persist tool boundaries but not ordinary text block endings."""
        message = {"id": "message-1", "role": "assistant"}
        tool_call = ToolCallBlock(
            id="call-1",
            name="Read",
            input='{"path":"README.md"}',
        )
        events = [
            TextBlockEndEvent(
                id="event-0",
                created_at="2026-01-01T00:00:00",
                reply_id="message-1",
                block_id="block-1",
            ),
            RequireUserConfirmEvent(
                id="event-1",
                created_at="2026-01-01T00:00:01",
                reply_id="message-1",
                tool_calls=[tool_call],
            ),
            ToolResultEndEvent(
                id="event-2",
                created_at="2026-01-01T00:00:02",
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

        session = _make_session(
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            on_closed=lambda _: closed.set(),
        )
        session.start()
        await asyncio.wait_for(closed.wait(), timeout=1)

        self.assertDictEqual(
            {
                "storage_methods": [call["method"] for call in storage.calls],
                "published_event_types": [
                    call["event"]["type"]
                    for call in message_bus.calls
                    if call["method"] == "publish"
                ],
                "cursor_values": [
                    call["value"]
                    for call in message_bus.calls
                    if call["method"] == "registry_set"
                ],
            },
            {
                "storage_methods": ["update_session_state"] * 3,
                "published_event_types": [event.type for event in events],
                "cursor_values": ["1-0"] * 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
