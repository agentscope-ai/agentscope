# -*- coding: utf-8 -*-
"""Remote media downloads must not block the formatter event loop."""
import asyncio
import threading

import pytest

from agentscope.formatter import (
    AnthropicChatFormatter,
    GeminiChatFormatter,
    MoonshotChatFormatter,
    OllamaChatFormatter,
    OpenAIChatFormatter,
    OpenAIResponseFormatter,
)
from agentscope.message import DataBlock, URLSource, UserMsg


class _Response:
    """Minimal requests response used by formatter download paths."""

    content = b"remote-media"

    @staticmethod
    def raise_for_status() -> None:
        """Model a successful response."""


@pytest.mark.parametrize(
    ("formatter_cls", "media_type"),
    [
        (OpenAIChatFormatter, "application/pdf"),
        (OpenAIResponseFormatter, "application/pdf"),
        (AnthropicChatFormatter, "image/png"),
        (GeminiChatFormatter, "image/png"),
        (MoonshotChatFormatter, "image/png"),
        (OllamaChatFormatter, "image/png"),
    ],
)
def test_remote_media_download_yields_to_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    formatter_cls: type,
    media_type: str,
) -> None:
    """A callback scheduled during the HTTP wait runs before it returns."""

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        loop_progressed: list[bool] = []

        def _get(*_args: object, **_kwargs: object) -> _Response:
            progressed = threading.Event()
            loop.call_soon_threadsafe(progressed.set)
            loop_progressed.append(progressed.wait(0.5))
            return _Response()

        monkeypatch.setattr("requests.get", _get)
        message = UserMsg(
            name="user",
            content=[
                DataBlock(
                    source=URLSource(
                        url="https://example.com/media",
                        media_type=media_type,
                    ),
                    name=(
                        "media.pdf"
                        if media_type == "application/pdf"
                        else None
                    ),
                ),
            ],
        )

        await formatter_cls().format([message])

        assert loop_progressed == [True]

    asyncio.run(_run())
