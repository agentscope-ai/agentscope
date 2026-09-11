# -*- coding: utf-8 -*-
"""Regression tests for realtime model reconnection lifecycle."""
# pylint: disable=protected-access
import asyncio
import json
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


class _BlockingWebSocket:
    """A socket that stays open after an optional first server frame."""

    def __init__(self, first_frame: dict | None = None) -> None:
        self._first_frame = first_frame
        self._closed = asyncio.Event()
        self.send = AsyncMock()

    def __aiter__(self) -> "_BlockingWebSocket":
        return self

    async def __anext__(self) -> str:
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return json.dumps(frame)
        await self._closed.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        """Release the blocked asynchronous iterator."""
        self._closed.set()


class RealtimeReconnectTest(IsolatedAsyncioTestCase):
    """Every provider isolates event queues between connections."""

    async def test_connect_replaces_previous_session_queue(self) -> None:
        """A stale end sentinel cannot terminate the next event stream."""
        cases = [
            (
                OpenAIRealtimeModel(
                    "gpt-realtime-2.1",
                    OpenAICredential(api_key="unused"),
                ),
                None,
            ),
            (
                GeminiRealtimeModel(
                    "gemini-3.1-flash-live-preview",
                    GeminiCredential(api_key="unused"),
                ),
                {"setupComplete": {}},
            ),
            (
                DashScopeRealtimeModel(
                    DashScopeRealtimeModel.list_models()[0].name,
                    DashScopeCredential(api_key="unused"),
                ),
                None,
            ),
            (
                XAIRealtimeModel(
                    XAIRealtimeModel.list_models()[0].name,
                    XAICredential(api_key="unused"),
                ),
                None,
            ),
        ]

        for model, first_frame in cases:
            with self.subTest(provider=model.type):
                previous_queue = model._queue
                previous_queue.put_nowait(
                    me.SessionEndedEvent(reason="previous close"),
                )
                previous_queue.put_nowait(None)
                socket = _BlockingWebSocket(first_frame)

                with patch(
                    "websockets.connect",
                    new=AsyncMock(return_value=socket),
                ):
                    await model.connect(instructions="test")

                self.assertIsNot(model._queue, previous_queue)
                self.assertTrue(model._queue.empty())
                await model.close()
