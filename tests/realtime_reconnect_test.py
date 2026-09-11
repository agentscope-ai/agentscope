# -*- coding: utf-8 -*-
"""Explicitly closing a realtime model and connecting again must give the new
session a clean event stream (issue #2587)."""
# pylint: disable=protected-access
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
        """Record what the model sends. Like a real socket write this
        yields to the event loop, which lets the reader task start."""
        self.sent.append(json.loads(payload))
        await asyncio.sleep(0)

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

    async def _connect(
        self,
        model: Any,
        frames: list[dict] | None = None,
    ) -> IdleSocket:
        """Connect the model to a fresh idle socket."""
        socket = IdleSocket(frames)
        with patch("websockets.connect", new=AsyncMock(return_value=socket)):
            await model.connect("Be brief.")
        self.assertIsNotNone(model._reader)
        return socket

    async def _collect_new_session(self, model: Any) -> list[me.ModelEvent]:
        """Feed one event of the new session, end it, and read the stream."""
        model._queue.put_nowait(me.ResponseCreatedEvent(item_id="item_new"))
        model._queue.put_nowait(None)
        got = [event async for event in model.events()]
        await model.close()
        return got

    async def _close_and_reconnect(
        self,
        model: Any,
        frames: list[dict] | None = None,
    ) -> list[me.ModelEvent]:
        """Run the real lifecycle: connect, close, connect again, then read
        what the second session's consumer sees."""
        await self._connect(model, frames)
        await model.close()
        await self._connect(model, frames)
        return await self._collect_new_session(model)

    async def test_openai_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        """OpenAI: the closed session's terminal events are not replayed."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            OpenAICredential(api_key="sk-x"),
        )
        got = await self._close_and_reconnect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_dashscope_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        """DashScope: the closed session's terminal events are not replayed."""
        model = DashScopeRealtimeModel(
            "qwen-omni-turbo-realtime",
            DashScopeCredential(api_key="sk-x"),
        )
        got = await self._close_and_reconnect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_xai_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        """xAI: the closed session's terminal events are not replayed."""
        model = XAIRealtimeModel(
            "grok-voice-latest",
            XAICredential(api_key="sk-x"),
        )
        got = await self._close_and_reconnect(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_gemini_reconnect_starts_with_the_new_sessions_events(
        self,
    ) -> None:
        """Gemini: the closed session's terminal events are not replayed."""
        model = GeminiRealtimeModel(
            "gemini-2.5-flash-native-audio-preview-12-2025",
            GeminiCredential(api_key="key-x"),
        )
        # connect() waits for the server's setupComplete before returning.
        got = await self._close_and_reconnect(
            model,
            frames=[{"setupComplete": {}}],
        )
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_reconnect_without_close_stops_the_old_reader(self) -> None:
        """connect() on a session whose reader is still running (half-open
        socket) cancels that reader first, so its terminal events cannot end
        the new stream and the task is not leaked."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            OpenAICredential(api_key="sk-x"),
        )
        await self._connect(model)
        old_reader = model._reader
        await self._connect(model)
        self.assertTrue(old_reader.done())
        self.assertIsNot(model._reader, old_reader)
        got = await self._collect_new_session(model)
        self.assertEqual(got, [me.ResponseCreatedEvent(item_id="item_new")])

    async def test_events_after_close_without_reconnect_still_end(
        self,
    ) -> None:
        """Draining happens only on connect(); a closed session still
        terminates its consumer with the terminal event."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            OpenAICredential(api_key="sk-x"),
        )
        await self._connect(model)
        await model.close()
        got = [event async for event in model.events()]
        self.assertEqual(got, [me.SessionEndedEvent(reason="closed")])
