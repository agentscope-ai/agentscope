# -*- coding: utf-8 -*-
"""Regression tests: remote media in async formatters must not block the
event loop.

Each test formats a message carrying a remote ``URLSource`` whose download is
replaced by a fake that blocks the calling thread. A concurrent heartbeat
coroutine increments a counter every few milliseconds. While the formatter
blocks the loop thread, the heartbeat cannot run; once the download is
offloaded with ``asyncio.to_thread`` the heartbeat keeps ticking.
"""
import asyncio
import time
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import requests

from agentscope.formatter import (
    AnthropicChatFormatter,
    GeminiChatFormatter,
    MoonshotChatFormatter,
    OllamaChatFormatter,
    OpenAIChatFormatter,
)
from agentscope.message import DataBlock, Msg, TextBlock, URLSource, UserMsg


_REMOTE_URL = "https://example.com/media.bin"


class _FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        """Match ``requests.Response.raise_for_status``."""


def _blocking_get(*_args: object, **_kwargs: object) -> _FakeResponse:
    """Simulate a slow download that blocks the calling thread for 200 ms."""
    time.sleep(0.2)
    return _FakeResponse(b"fake-binary-payload")


class _EventLoopHeartbeatMixin:
    """Assert that a concurrent coroutine keeps running during ``format``."""

    formatter = None
    media_type = ""

    def _message(self) -> Msg:
        return UserMsg(
            name="user",
            content=[
                TextBlock(text="describe this"),
                DataBlock(
                    source=URLSource(
                        url=_REMOTE_URL,
                        media_type=self.media_type,
                    ),
                ),
            ],
        )

    async def test_remote_media_does_not_block_event_loop(self) -> None:
        """A concurrent coroutine must make progress while media is fetched."""
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        with patch.object(requests, "get", _blocking_get):
            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.02)  # let the heartbeat start
            baseline = ticks

            await self.formatter.format([self._message()])

            elapsed_ticks = ticks - baseline
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)

        # 200 ms of downloading at a 10 ms tick is ~20 ticks. Anything above
        # a handful proves the loop was not stalled for the whole download.
        self.assertGreater(
            elapsed_ticks,
            5,
            "event loop was blocked while downloading remote media",
        )


class TestOpenAIChatFormatterOffload(
    _EventLoopHeartbeatMixin,
    IsolatedAsyncioTestCase,
):
    """OpenAI audio downloads must run off the event-loop thread."""

    formatter = OpenAIChatFormatter()
    media_type = "audio/wav"


class TestAnthropicFormatterOffload(
    _EventLoopHeartbeatMixin,
    IsolatedAsyncioTestCase,
):
    """Anthropic media downloads must run off the event-loop thread."""

    formatter = AnthropicChatFormatter()
    media_type = "image/png"


class TestGeminiFormatterOffload(
    _EventLoopHeartbeatMixin,
    IsolatedAsyncioTestCase,
):
    """Gemini media downloads must run off the event-loop thread."""

    formatter = GeminiChatFormatter()
    media_type = "image/png"


class TestMoonshotFormatterOffload(
    _EventLoopHeartbeatMixin,
    IsolatedAsyncioTestCase,
):
    """Moonshot media downloads must run off the event-loop thread."""

    formatter = MoonshotChatFormatter()
    media_type = "image/png"


class TestOllamaFormatterOffload(
    _EventLoopHeartbeatMixin,
    IsolatedAsyncioTestCase,
):
    """Ollama image downloads must run off the event-loop thread."""

    formatter = OllamaChatFormatter()
    media_type = "image/png"
