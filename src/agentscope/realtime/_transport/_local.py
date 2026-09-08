# -*- coding: utf-8 -*-
"""The local sound-card transport."""
import asyncio
import threading
import time
from typing import Any, AsyncIterator

import numpy as np

from ._base import AudioFrame, TransportBase, TransportFrame
from .._playout import PlayoutPosition
from ..._logging import logger


class LocalAudioTransport(TransportBase):
    """Microphone in, speaker out, via ``sounddevice``.

    Audio callbacks run on PortAudio's thread, not the event loop: captured
    chunks are handed to the loop with ``call_soon_threadsafe`` and
    playback pulls from a lock-guarded buffer. Playout accounting lives in
    that callback, the same place a browser's AudioWorklet does it.
    """

    def __init__(
        self,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        chunk_ms: int = 100,
        fade_ms: int = 30,
    ) -> None:
        """Initialize the transport.

        Args:
            input_sample_rate (`int`, defaults to `16000`):
                Capture rate; should match the model's input rate.
            output_sample_rate (`int`, defaults to `24000`):
                Playback rate; should match the model's output rate.
            input_device (`int | str | None`, optional):
                ``sounddevice`` input device, default if ``None``.
            output_device (`int | str | None`, optional):
                ``sounddevice`` output device, default if ``None``.
            chunk_ms (`int`, defaults to `100`):
                Capture chunk length handed to :meth:`incoming`.
            fade_ms (`int`, defaults to `30`):
                Fade-out applied by :meth:`clear_audio` to avoid a click.
        """
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self._input_device = input_device
        self._output_device = output_device
        self._chunk_frames = input_sample_rate * chunk_ms // 1000
        self._fade_ms = fade_ms
        # Ten seconds of capture; beyond that the oldest chunk is dropped.
        self._max_queued = max(1, 10_000 // chunk_ms)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._in_queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue()
        self._in_stream: Any = None
        self._out_stream: Any = None

        # Playback state, guarded by ``_lock`` (shared with the audio thread).
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._item_id = ""
        self._played_samples = 0
        self._first_played_at: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open both streams."""
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self._in_stream = sd.InputStream(
            samplerate=self.input_sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self._chunk_frames,
            device=self._input_device,
            callback=self._on_input,
        )
        self._out_stream = sd.OutputStream(
            samplerate=self.output_sample_rate,
            channels=1,
            dtype="int16",
            device=self._output_device,
            callback=self._on_output,
        )
        self._in_stream.start()
        self._out_stream.start()

    async def close(self) -> None:
        """Stop both streams and end :meth:`incoming`."""
        for stream in (self._in_stream, self._out_stream):
            if stream is not None:
                stream.stop()
                stream.close()
        self._in_stream = self._out_stream = None
        self._in_queue.put_nowait(None)

    # ------------------------------------------------------------------
    # Upstream
    # ------------------------------------------------------------------

    async def incoming(self) -> AsyncIterator[TransportFrame]:
        """Yield captured chunks until :meth:`close`."""
        while True:
            frame = await self._in_queue.get()
            if frame is None:
                return
            yield frame

    def _on_input(
        self,
        indata: Any,
        _frames: int,
        _t: Any,
        status: Any,
    ) -> None:
        """Audio thread: hand one captured chunk to the loop."""
        if status:
            logger.debug("LocalAudioTransport: input %s", status)
        if self._loop is None:
            return
        frame = AudioFrame(pcm=bytes(indata))
        self._loop.call_soon_threadsafe(self._enqueue, frame)

    def _enqueue(self, frame: AudioFrame) -> None:
        """Loop thread: queue a chunk, dropping the oldest when nobody drains
        fast enough, so a stalled consumer never replays stale audio."""
        if self._in_queue.qsize() >= self._max_queued:
            self._in_queue.get_nowait()
        self._in_queue.put_nowait(frame)

    # ------------------------------------------------------------------
    # Downstream
    # ------------------------------------------------------------------

    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Queue assistant audio. A new item resets the playout counters."""
        with self._lock:
            if item_id != self._item_id:
                self._item_id = item_id
                self._pending.clear()
                self._played_samples = 0
                self._first_played_at = None
            self._pending.extend(pcm)

    async def send_event(self, event: dict) -> None:
        """No peer to send to; events are consumed via the agent."""

    def _on_output(
        self,
        outdata: Any,
        frames: int,
        _t: Any,
        status: Any,
    ) -> None:
        """Audio thread: fill one block from the buffer, count what played."""
        if status:
            logger.debug("LocalAudioTransport: output %s", status)
        wanted = frames * 2
        with self._lock:
            chunk = bytes(self._pending[:wanted])
            del self._pending[:wanted]
            if chunk:
                self._played_samples += len(chunk) // 2
                if self._first_played_at is None:
                    self._first_played_at = time.monotonic()
        n = len(chunk) // 2  # bytes -> int16 samples
        outdata[:n] = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)
        outdata[n:] = 0

    async def clear_audio(self) -> PlayoutPosition:
        """Fade the head of the queue out, drop the rest, report the cut."""
        with self._lock:
            position = self._position()
            fade_bytes = self.output_sample_rate * self._fade_ms // 1000 * 2
            head = np.frombuffer(bytes(self._pending[:fade_bytes]), np.int16)
            self._pending.clear()
            if len(head):
                ramp = np.linspace(1.0, 0.0, len(head), dtype=np.float32)
                self._pending.extend((head * ramp).astype(np.int16).tobytes())
        return position

    def playout(self) -> PlayoutPosition:
        """The current playback position."""
        with self._lock:
            return self._position()

    def _position(self) -> PlayoutPosition:
        """Build a position; caller holds the lock."""
        return PlayoutPosition(
            item_id=self._item_id,
            played_ms=self._played_samples * 1000 // self.output_sample_rate,
            first_played_at=self._first_played_at,
        )
