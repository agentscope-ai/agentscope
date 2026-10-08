# -*- coding: utf-8 -*-
"""Audio utilities shared across model providers."""
import struct


def _wav_header(
    riff_size: int,
    data_size: int,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a 44-byte WAV/RIFF header with explicit chunk sizes.

    Args:
        riff_size (`int`):
            The size to declare for the RIFF chunk.
        data_size (`int`):
            The size to declare for the ``data`` chunk.

    Returns:
        `bytes`:
            A 44-byte WAV header.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
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
        + struct.pack("<I", data_size)
    )


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

    For a *complete* buffer use :func:`_build_wav_header` instead, which
    declares real chunk sizes.
    """
    return _wav_header(
        0xFFFFFFFF,
        0xFFFFFFFF,
        sample_rate,
        channels,
        bits_per_sample,
    )


def _build_wav_header(
    data_len: int,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a 44-byte WAV/RIFF header for a complete PCM buffer.

    Unlike :func:`_build_streaming_wav_header`, the RIFF and ``data`` chunk
    sizes are real, so the result is a well-formed WAV that ordinary decoders
    and file-based players accept, not only a live-stream player.
    """
    return _wav_header(
        36 + data_len,
        data_len,
        sample_rate,
        channels,
        bits_per_sample,
    )
