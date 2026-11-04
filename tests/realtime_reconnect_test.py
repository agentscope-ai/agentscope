# -*- coding: utf-8 -*-
"""Reconnecting a realtime model must give the new session a clean event
stream (issue #2587)."""
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
    RealtimeModelBase,
    XAIRealtimeModel,
)
from agentscope.realtime import _events as me


class IdleSocket:
    """A WebSocket that replays a few frames, then stays open until closed."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = [json.dumps(_) for _ in frames]
        self._closed = asyncio.Event()
        self.sent: list[Any] = []

    async def send(self, payload: str) -> None:
        """Record the payload and yield like a real write, so the reader
        task gets to start."""
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
    """The previous session must not end or break the new one."""

    def setUp(self) -> None:
        """Build one model per provider; Gemini's connect() waits for
        setupComplete, so its socket replays that frame."""
        self.models: list[tuple[RealtimeModelBase, list[dict]]] = [
            (
                OpenAIRealtimeModel(
                    "gpt-realtime-1.5",
                    OpenAICredential(api_key="sk-x"),
                ),
                [],
            ),
            (
                DashScopeRealtimeModel(
                    "qwen-omni-turbo-realtime",
                    DashScopeCredential(api_key="sk-x"),
                ),
                [{"type": "session.updated"}],
            ),
            (
                XAIRealtimeModel(
                    "grok-voice-latest",
                    XAICredential(api_key="sk-x"),
                ),
                [],
            ),
            (
                GeminiRealtimeModel(
                    "gemini-2.5-flash-native-audio-preview-12-2025",
                    GeminiCredential(api_key="key-x"),
                ),
                [{"setupComplete": {}}],
            ),
        ]

    async def _connect(self, model: Any, frames: list[dict]) -> IdleSocket:
        """Connect the model to a fresh idle socket."""
        socket = IdleSocket(frames)
        with patch("websockets.connect", new=AsyncMock(return_value=socket)):
            await model.connect("Be brief.")
        return socket

    async def _read_new_session(self, model: Any) -> list[me.ModelEvent]:
        """Feed one event of the new session, end it, and read the stream."""
        model._queue.put_nowait(me.ResponseCreatedEvent(item_id="item_new"))
        model._queue.put_nowait(None)
        got = [event async for event in model.events()]
        await model.close()
        return got

    async def test_reconnect_after_close(self) -> None:
        """connect -> close -> connect: the closed session's terminal events
        are not replayed to the new stream."""
        for model, frames in self.models:
            with self.subTest(model=type(model).__name__):
                await self._connect(model, frames)
                await model.close()
                await self._connect(model, frames)
                self.assertListEqual(
                    await self._read_new_session(model),
                    [me.ResponseCreatedEvent(item_id="item_new")],
                )

    async def test_reconnect_without_close(self) -> None:
        """connect -> connect: the old reader and socket are stopped before
        the new session starts, so they can't end or break it."""
        for model, frames in self.models:
            with self.subTest(model=type(model).__name__):
                old_socket = await self._connect(model, frames)
                old_reader = model._reader
                new_socket = await self._connect(model, frames)
                self.assertTrue(old_reader.done())
                self.assertTrue(old_socket._closed.is_set())
                self.assertIs(model._ws, new_socket)
                self.assertListEqual(
                    await self._read_new_session(model),
                    [me.ResponseCreatedEvent(item_id="item_new")],
                )

    async def test_events_after_close_without_reconnect_still_end(
        self,
    ) -> None:
        """Draining happens only on connect(); a closed session still
        terminates its consumer with the terminal event."""
        for model, frames in self.models:
            with self.subTest(model=type(model).__name__):
                await self._connect(model, frames)
                await model.close()
                self.assertListEqual(
                    [event async for event in model.events()],
                    [me.SessionEndedEvent(reason="closed")],
                )
