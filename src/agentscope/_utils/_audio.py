# -*- coding: utf-8 -*-
"""Audio utilities shared across model providers."""
import struct


def _build_streaming_wav_header(
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a 44-byte WAV/RIFF header for streaming PCM.

    The RIFF and ``data`` chunk sizes are set to ``0xFFFFFFFF`` since the
    total length isn't known yet. Decoders that only need sample-rate,
    channel count and bit depth (e.g. the web ``WavStreamPlayer``) treat
    everything after the ``data`` chunk header as PCM, so this is
    sufficient for live decoding of an open-ended stream.

    Both DashScope omni and OpenAI streaming deliver raw PCM upstream;
    prefixing the first chunk with this header lets the frontend start
    playback immediately without buffering the whole response.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack(
            "<HHIIHH",
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )
