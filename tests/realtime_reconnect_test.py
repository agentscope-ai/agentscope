# -*- coding: utf-8 -*-
"""Explicitly closing a realtime model and connecting again must give the new
session a clean event stream (issue #2587)."""

import asyncio
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.credential import (
    DashScopeCredential,
    GeminiCredential,
    OpenAICredential,
    XAICredential,
)
from agentscope.realtime import (
    DashScopeRealtimeModel,
    GeminiRealtimeModel,
    OpenAIRealtimeModel,
    XAIRealtimeModel,
)
from agentscope.realtime import _events as me


class IdleSocket:
    """A WebSocket that replays a few frames, then stays open until closed."""

    def __init__(self, frames: list[dict] | None = None) -> None:
        self._frames = [json.dumps(_) for _ in (frames or [])]
        self._closed = asyncio.Event()
        self.sent: list[Any] = []

    async def send(self, payload: str) -> None:
        """Record what the model sends."""
        self.sent.append(json.loads(payload))

    async def close(self) -> None:
        """Release the reader."""
        self._closed.set()

    def __aiter__(self) -> "IdleSocket":
        return self

    async def __anext__(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await self._closed.wait()
        raise StopAsyncIteration


class ReconnectTest(IsolatedAsyncioTestCase):
    """The previous session's terminal events must not end the new stream."""

    async def _reconnect_and_collect(
        self,
        model: Any,
        frames: list[dict] | None = None,
    ) -> list[me.ModelEvent]:
        # What close() leaves behind: the reader's finally block enqueues the
        # terminal event and the end-of-stream sentinel.
        model._queue.put_nowait(me.SessionEndedEvent(reason="closed"))
        model._queue.put_nowait(None)

        socket = IdleSocket(frames)
        with patch("websockets.connect", new=AsyncMock(return_value=socket)):
            await model.connect("Be brief.")
        self.assertIsNotNone(model._reader)

        fresh = me.ResponseCreatedEvent(item_id="item_new")
        model._queue.put_nowait(fresh)
        model._queue.put_nowait(None)
        got = [event async for event in model.events()]
        await model.close()
        return got

    async def test_openai_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            OpenAICredential(api_key="sk-x"),
        )
        got = await self._reconnect_and_collect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_dashscope_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        model = DashScopeRealtimeModel(
            "qwen-omni-turbo-realtime",
            DashScopeCredential(api_key="sk-x"),
        )
        got = await self._reconnect_and_collect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_xai_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        model = XAIRealtimeModel(
            "grok-voice-latest",
            XAICredential(api_key="sk-x"),
        )
        got = await self._reconnect_and_collect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_gemini_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        model = GeminiRealtimeModel(
            "gemini-2.5-flash-native-audio-preview-12-2025",
            GeminiCredential(api_key="key-x"),
        )
        # connect() waits for the server's setupComplete before returning.
        got = await self._reconnect_and_collect(
            model,
            frames=[{"setupComplete": {}}],
        )
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_events_after_close_without_reconnect_still_end(
        self,
    ) -> None:
        """Draining happens only on connect(); a closed session still
        terminates its consumer."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            OpenAICredential(api_key="sk-x"),
        )
        model._queue.put_nowait(me.SessionEndedEvent(reason="closed"))
        model._queue.put_nowait(None)
        got = [event async for event in model.events()]
        self.assertEqual(got, [me.SessionEndedEvent(reason="closed")])
