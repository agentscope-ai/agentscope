"""Regression tests: media-block formatting must not block the event loop.

The URLSource helpers used to run ``requests.get`` and local file reads
directly on the loop, freezing everything else on it for up to the 30s HTTP
timeout per media block. Each test runs format() with a fake 1s download and
counts event-loop ticks of a concurrent task: a blocked loop cannot tick.
"""

import asyncio
import base64
import time
from unittest.mock import patch

import pytest

from agentscope.formatter import (
    AnthropicChatFormatter,
    GeminiChatFormatter,
    MoonshotChatFormatter,
    OllamaChatFormatter,
    OpenAIChatFormatter,
)
from agentscope.message import DataBlock, Msg, TextBlock, URLSource


def _slow_get(*args, **kwargs):
    time.sleep(1.0)
    raise RuntimeError("fake download finished")


async def _assert_loop_stays_responsive(formatter, patch_target, msg):
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.05)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.1)
    base = ticks
    with patch(patch_target, _slow_get):
        try:
            await formatter.format([msg])
        except (
            Exception
        ):  # noqa: BLE001, S110  the fake download fails on purpose
            pass
    task.cancel()
    assert ticks - base >= 5, (
        f"event loop blocked during format(): only {ticks - base} ticks "
        "in over a second"
    )


def _audio_msg():
    return Msg(
        name="user",
        role="user",
        content=[
            TextBlock(text="hi"),
            DataBlock(
                source=URLSource(
                    url="http://example.invalid/a.mp3",
                    media_type="audio/mpeg",
                ),
            ),
        ],
    )


def _image_msg():
    return Msg(
        name="user",
        role="user",
        content=[
            DataBlock(
                source=URLSource(
                    url="http://example.invalid/i.png",
                    media_type="image/png",
                ),
            ),
        ],
    )


@pytest.mark.asyncio
async def test_openai_chat_audio_download_does_not_block_loop():
    await _assert_loop_stays_responsive(
        OpenAIChatFormatter(),
        "agentscope.formatter._openai_formatter.requests.get",
        _audio_msg(),
    )


@pytest.mark.asyncio
async def test_gemini_media_download_does_not_block_loop():
    await _assert_loop_stays_responsive(
        GeminiChatFormatter(),
        "agentscope.formatter._gemini_formatter.requests.get",
        _audio_msg(),
    )


@pytest.mark.asyncio
async def test_moonshot_media_download_does_not_block_loop():
    await _assert_loop_stays_responsive(
        MoonshotChatFormatter(),
        "agentscope.formatter._moonshot_formatter.requests.get",
        _image_msg(),
    )


@pytest.mark.asyncio
async def test_anthropic_image_download_does_not_block_loop():
    await _assert_loop_stays_responsive(
        AnthropicChatFormatter(),
        "agentscope.formatter._anthropic_formatter.requests.get",
        _image_msg(),
    )


@pytest.mark.asyncio
async def test_ollama_image_download_does_not_block_loop():
    await _assert_loop_stays_responsive(
        OllamaChatFormatter(),
        "agentscope.formatter._ollama_formatter.requests.get",
        _image_msg(),
    )


@pytest.mark.asyncio
async def test_output_unchanged_for_remote_audio():
    """The refactor must keep the emitted payload identical."""

    def fake_get(*args, **kwargs):
        class R:
            content = b"fake audio"

            def raise_for_status(self):
                pass

        return R()

    with patch(
        "agentscope.formatter._openai_formatter.requests.get",
        fake_get,
    ):
        res = await OpenAIChatFormatter().format([_audio_msg()])
    assert res[0]["content"][1] == {
        "type": "input_audio",
        "input_audio": {
            "data": base64.b64encode(b"fake audio").decode("utf-8"),
            "format": "mp3",
        },
    }
