# -*- coding: utf-8 -*-
"""WebRTC media and control transport for browser voice sessions."""

import asyncio
import json
import time
import uuid
from collections import deque
from fractions import Fraction
from typing import AsyncIterator, Callable

import numpy as np
from aiortc import MediaStreamTrack, RTCDataChannel
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame as AVAudioFrame
from av import AudioResampler

from ...realtime import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    PlayoutPosition,
    TransportBase,
    TransportFrame,
)


_END = object()
_CLEAR_TIMEOUT_SECONDS = 2.0
_WEBRTC_SAMPLE_RATE = 48_000
_FRAME_DURATION_MS = 20
_FRAME_SAMPLES = _WEBRTC_SAMPLE_RATE * _FRAME_DURATION_MS // 1_000
_FRAME_BYTES = _FRAME_SAMPLES * 2
_MAX_INCOMING_AUDIO_MS = 10_000


class WebRTCAudioTrack(MediaStreamTrack):
    """A continuous mono audio track fed by model PCM chunks."""

    kind = "audio"

    def __init__(
        self,
        source_sample_rate: int,
        on_item_started: Callable[[str, int], None],
    ) -> None:
        super().__init__()
        self._source_sample_rate = source_sample_rate
        self._on_item_started = on_item_started
        self._resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=_WEBRTC_SAMPLE_RATE,
        )
        self._chunks: deque[tuple[str, bytearray]] = deque()
        self._started_items: set[str] = set()
        self._pts = 0
        self._started_at: float | None = None

    def push(self, pcm: bytes, item_id: str) -> None:
        """Resample and queue one model PCM16 chunk."""
        if not pcm:
            return
        samples = np.frombuffer(pcm, dtype="<i2").reshape(1, -1)
        frame = AVAudioFrame.from_ndarray(
            samples,
            format="s16",
            layout="mono",
        )
        frame.sample_rate = self._source_sample_rate
        for converted in self._resampler.resample(frame):
            data = converted.to_ndarray().astype("<i2", copy=False).tobytes()
            if data:
                self._chunks.append((item_id, bytearray(data)))

    def clear(self) -> int:
        """Discard queued audio and return the next silent track time."""
        self._chunks.clear()
        self._resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=_WEBRTC_SAMPLE_RATE,
        )
        return self._pts * 1_000 // _WEBRTC_SAMPLE_RATE

    async def recv(self) -> AVAudioFrame:
        """Return one paced 20 ms frame, using silence while idle."""
        if self._started_at is None:
            self._started_at = time.monotonic()
        target = self._started_at + self._pts / _WEBRTC_SAMPLE_RATE
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        payload = bytearray()
        frame_item = ""
        while self._chunks and len(payload) < _FRAME_BYTES:
            item_id, chunk = self._chunks[0]
            if frame_item and item_id != frame_item:
                break
            frame_item = item_id
            remaining = _FRAME_BYTES - len(payload)
            payload.extend(chunk[:remaining])
            del chunk[:remaining]
            if not chunk:
                self._chunks.popleft()

        if frame_item and frame_item not in self._started_items:
            self._started_items.add(frame_item)
            self._on_item_started(
                frame_item,
                self._pts * 1_000 // _WEBRTC_SAMPLE_RATE,
            )

        if len(payload) < _FRAME_BYTES:
            payload.extend(b"\x00" * (_FRAME_BYTES - len(payload)))
        samples = np.frombuffer(payload, dtype="<i2").reshape(1, -1)
        frame = AVAudioFrame.from_ndarray(
            samples,
            format="s16",
            layout="mono",
        )
        frame.sample_rate = _WEBRTC_SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = Fraction(1, _WEBRTC_SAMPLE_RATE)
        self._pts += _FRAME_SAMPLES
        return frame


class WebRTCAudioTransport(TransportBase):
    """Bridge WebRTC audio tracks and a control DataChannel to an agent."""

    def __init__(
        self,
        input_sample_rate: int,
        output_sample_rate: int,
    ) -> None:
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.output_track = WebRTCAudioTrack(
            source_sample_rate=output_sample_rate,
            on_item_started=self._on_item_started,
        )
        self._incoming: deque[TransportFrame | object] = deque()
        self._incoming_ready = asyncio.Event()
        self._queued_audio_frames = 0
        self._max_queued_audio_frames = max(
            1,
            _MAX_INCOMING_AUDIO_MS // _FRAME_DURATION_MS,
        )
        self._input_task: asyncio.Task | None = None
        self._channel: RTCDataChannel | None = None
        self._position = PlayoutPosition(item_id="")
        self._audio_bytes: dict[str, int] = {}
        self._latest_item_id = ""
        self._blocked_item_id = ""
        self._clear_waiters: dict[
            str,
            asyncio.Future[PlayoutPosition],
        ] = {}
        self._closed = False
        self.on_disconnect: Callable[[], None] | None = None

    async def start(self) -> None:
        """Mark the transport ready; WebRTC resources are attached later."""

    async def close(self) -> None:
        """Stop media work and unblock the agent's incoming iterator."""
        if self._closed:
            return
        self._closed = True
        if self._input_task is not None:
            self._input_task.cancel()
            await asyncio.gather(self._input_task, return_exceptions=True)
            self._input_task = None
        self.output_track.stop()
        for waiter in self._clear_waiters.values():
            if not waiter.done():
                waiter.set_result(self.playout())
        self._clear_waiters.clear()
        self._incoming.append(_END)
        self._incoming_ready.set()

    def set_input_track(self, track: MediaStreamTrack) -> None:
        """Attach the browser microphone track."""
        if self._input_task is not None:
            self._input_task.cancel()
        self._input_task = asyncio.create_task(
            self._read_input(track),
            name="webrtc-audio-input",
        )

    def set_data_channel(self, channel: RTCDataChannel) -> None:
        """Attach the browser-created control DataChannel."""
        self._channel = channel

        @channel.on("message")
        def _on_message(message: str | bytes) -> None:
            if isinstance(message, str):
                self._handle_json(message)

        @channel.on("close")
        def _on_close() -> None:
            self._notify_disconnect()

    async def incoming(self) -> AsyncIterator[TransportFrame]:
        """Yield decoded microphone PCM and control frames."""
        while True:
            while not self._incoming:
                self._incoming_ready.clear()
                await self._incoming_ready.wait()
            frame = self._incoming.popleft()
            if isinstance(frame, AudioFrame):
                self._queued_audio_frames -= 1
            if frame is _END:
                return
            yield frame  # type: ignore[misc]

    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Queue assistant PCM on the outgoing WebRTC audio track."""
        if self._blocked_item_id:
            if item_id == self._blocked_item_id:
                return
            self._blocked_item_id = ""
        self._latest_item_id = item_id
        self.output_track.push(pcm, item_id)
        self._audio_bytes[item_id] = self._audio_bytes.get(item_id, 0) + len(
            pcm,
        )
        duration_ms = round(
            self._audio_bytes[item_id] / (self.output_sample_rate * 2) * 1_000,
        )
        self._send_json(
            {
                "type": "audio_duration",
                "item_id": item_id,
                "duration_ms": duration_ms,
            },
        )

    async def clear_audio(self) -> PlayoutPosition:
        """Stop queued media and ask the browser what was audible."""
        self._blocked_item_id = self._latest_item_id
        resume_track_time_ms = self.output_track.clear()
        if self._channel is None or self._channel.readyState != "open":
            return self._take_playout()
        request_id = uuid.uuid4().hex
        waiter = asyncio.get_running_loop().create_future()
        self._clear_waiters[request_id] = waiter
        self._send_json(
            {
                "type": "clear_audio",
                "request_id": request_id,
                "resume_track_time_ms": resume_track_time_ms,
            },
        )
        try:
            return await asyncio.wait_for(
                waiter,
                timeout=_CLEAR_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return self._take_playout()
        finally:
            self._clear_waiters.pop(request_id, None)

    def playout(self) -> PlayoutPosition:
        """Return the browser's latest playback report."""
        return self._position.model_copy()

    def send_error(self, detail: str) -> None:
        """Send a protocol error over the control channel."""
        self._send_json({"type": "error", "detail": detail})

    def _on_item_started(self, item_id: str, track_time_ms: int) -> None:
        """Tell the browser where an item begins on the media timeline."""
        self._position = PlayoutPosition(item_id=item_id)
        self._send_json(
            {
                "type": "audio_start",
                "item_id": item_id,
                "track_time_ms": track_time_ms,
            },
        )

    def _send_json(self, payload: dict) -> None:
        """Send a control message when the DataChannel is open."""
        if self._channel is not None and self._channel.readyState == "open":
            self._channel.send(json.dumps(payload))

    async def _read_input(self, track: MediaStreamTrack) -> None:
        """Decode and resample the browser microphone into model PCM."""
        resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=self.input_sample_rate,
        )
        try:
            while True:
                frame = await track.recv()
                for converted in resampler.resample(frame):
                    pcm = (
                        converted.to_ndarray()
                        .astype("<i2", copy=False)
                        .tobytes()
                    )
                    if pcm:
                        self._enqueue_incoming(AudioFrame(pcm=pcm))
        except (MediaStreamError, asyncio.CancelledError):
            pass
        finally:
            if not self._closed:
                self._notify_disconnect()

    def _enqueue_incoming(self, frame: TransportFrame) -> None:
        """Queue one frame, dropping only stale audio when audio is full."""
        if self._closed:
            return
        if isinstance(frame, AudioFrame):
            if self._queued_audio_frames >= self._max_queued_audio_frames:
                for index, queued in enumerate(self._incoming):
                    if isinstance(queued, AudioFrame):
                        del self._incoming[index]
                        self._queued_audio_frames -= 1
                        break
            self._queued_audio_frames += 1
        self._incoming.append(frame)
        self._incoming_ready.set()

    def _handle_json(self, raw: str) -> None:
        """Handle one DataChannel control or playback report."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error("DataChannel messages must be JSON.")
            return
        if not isinstance(payload, dict):
            self.send_error("DataChannel JSON messages must be objects.")
            return

        frame_type = payload.get("type")
        if frame_type == "control":
            try:
                control = ControlFrameType(payload.get("control"))
            except ValueError:
                self.send_error("Unknown realtime control frame.")
                return
            data = payload.get("data", {})
            if not isinstance(data, dict):
                self.send_error("Control frame data must be an object.")
                return
            self._enqueue_incoming(ControlFrame(type=control, data=data))
            return

        if frame_type == "playout":
            self._update_playout(payload)
            return

        if frame_type == "playout_cleared":
            request_id = payload.get("request_id")
            waiter = (
                self._clear_waiters.get(request_id)
                if isinstance(request_id, str)
                else None
            )
            if waiter is None or waiter.done():
                return
            self._update_playout(payload)
            waiter.set_result(self._take_playout())
            return

        if frame_type == "close":
            self._notify_disconnect()
            return

        self.send_error("Unknown realtime frame type.")

    def _notify_disconnect(self) -> None:
        """Notify the session owner that its peer is gone."""
        callback = self.on_disconnect
        if callback is not None:
            callback()

    def _update_playout(self, payload: dict) -> None:
        """Record the browser's item-aware playback position."""
        item_id = payload.get("item_id")
        played_ms = payload.get("played_ms")
        if not isinstance(item_id, str) or not isinstance(played_ms, int):
            return
        if played_ms < 0:
            return
        if not item_id and self._position.item_id:
            item_id = self._position.item_id
        first_played_at = self._position.first_played_at
        if item_id != self._position.item_id:
            first_played_at = None
        if played_ms > 0 and first_played_at is None:
            first_played_at = time.monotonic()
        self._position = PlayoutPosition(
            item_id=item_id,
            played_ms=played_ms,
            first_played_at=first_played_at,
        )

    def _take_playout(self) -> PlayoutPosition:
        """Return the current position and begin a fresh playback epoch."""
        position = self.playout()
        self._position = PlayoutPosition(item_id="")
        return position
