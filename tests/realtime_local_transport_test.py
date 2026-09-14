# -*- coding: utf-8 -*-
"""Unit tests for LocalAudioTransport, driving the PortAudio callbacks
directly — no sound card is opened."""
# pylint: disable=protected-access
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

import numpy as np

from agentscope.realtime import (
    AudioFrame,
    LocalAudioTransport,
    PlayoutPosition,
)

BLOCK = 2400  # 100 ms at 24 kHz


def _play(transport: LocalAudioTransport, blocks: int) -> np.ndarray:
    """Run the output callback *blocks* times and return the last block."""
    out = np.zeros((BLOCK, 1), np.int16)
    for _ in range(blocks):
        transport._on_output(out, BLOCK, None, None)
    return out


class LocalAudioTransportTest(IsolatedAsyncioTestCase):
    """Playback buffering, playout accounting and the interruption fade."""

    async def test_playout_accounting_and_first_played_at(self) -> None:
        """Position advances by what the callback consumed, and the first
        played timestamp is set once."""
        transport = LocalAudioTransport(output_sample_rate=24000)
        await transport.send_audio(
            np.full(24000, 1000, np.int16).tobytes(),
            "r1",
        )

        self.assertEqual(
            transport.playout().model_dump(),
            {"item_id": "r1", "played_ms": 0, "first_played_at": None},
        )
        _play(transport, 5)
        position = transport.playout()
        self.assertEqual(
            (
                position.item_id,
                position.played_ms,
                position.first_played_at is not None,
            ),
            ("r1", 500, True),
        )

    async def test_clear_audio_reports_cut_then_fades(self) -> None:
        """The reported position excludes the fade tail; the tail ramps to
        silence and the remainder of the block is zero-filled."""
        transport = LocalAudioTransport(output_sample_rate=24000, fade_ms=30)
        await transport.send_audio(
            np.full(24000, 1000, np.int16).tobytes(),
            "r1",
        )
        _play(transport, 5)

        cut = await transport.clear_audio()
        tail = np.frombuffer(bytes(transport._pending), np.int16)
        self.assertEqual(
            (
                cut.played_ms,
                len(tail),
                int(tail[0]),
                int(tail[-1]),
                bool(np.all(np.diff(tail) <= 0)),
            ),
            (500, 720, 1000, 0, True),
        )

        out = _play(transport, 1)
        self.assertEqual(
            (
                len(transport._pending),
                int(out[0, 0]),
                int(out[719, 0]),
                int(out[720, 0]),
                int(out[-1, 0]),
            ),
            (0, 1000, 0, 0, 0),
        )

    async def test_new_item_resets_counters(self) -> None:
        """Audio for a new item starts a fresh position."""
        transport = LocalAudioTransport(output_sample_rate=24000)
        await transport.send_audio(
            np.full(24000, 1000, np.int16).tobytes(),
            "r1",
        )
        _play(transport, 3)
        await transport.send_audio(b"\x00\x00" * 10, "r2")

        self.assertEqual(
            transport.playout().model_dump(),
            {"item_id": "r2", "played_ms": 0, "first_played_at": None},
        )

    async def test_partial_block_is_zero_filled(self) -> None:
        """A block larger than what is queued plays the remainder as silence
        and counts only what was queued."""
        transport = LocalAudioTransport(output_sample_rate=24000)
        await transport.send_audio(np.full(600, 7, np.int16).tobytes(), "r1")

        out = _play(transport, 1)
        self.assertEqual(
            (
                int(out[0, 0]),
                int(out[599, 0]),
                int(out[600, 0]),
                transport.playout().played_ms,
            ),
            (7, 7, 0, 25),
        )

    async def test_input_queue_drops_oldest_when_full(self) -> None:
        """Capture never grows past ten seconds; the oldest chunk goes."""
        transport = LocalAudioTransport(input_sample_rate=16000, chunk_ms=100)
        transport._loop = asyncio.get_running_loop()
        for i in range(transport._max_queued + 3):
            transport._enqueue(AudioFrame(pcm=bytes([i % 256]) * 2))

        queued = []
        while not transport._in_queue.empty():
            queued.append(transport._in_queue.get_nowait().pcm[0])
        self.assertEqual(
            (len(queued), queued[0], queued[-1]),
            (transport._max_queued, 3, (transport._max_queued + 2) % 256),
        )

    async def test_playout_position_shape(self) -> None:
        """The position is a plain value object."""
        self.assertEqual(
            PlayoutPosition(
                item_id="x",
                played_ms=1,
                first_played_at=2.0,
            ).model_dump(),
            {"item_id": "x", "played_ms": 1, "first_played_at": 2.0},
        )
