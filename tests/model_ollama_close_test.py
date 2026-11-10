# -*- coding: utf-8 -*-
"""Exercise native Ollama stream ownership through its real HTTP transport."""
import asyncio
import json
from collections.abc import AsyncIterator
from importlib.util import find_spec
from unittest import IsolatedAsyncioTestCase, skipUnless

import httpx

from agentscope.credential import OllamaCredential
from agentscope.model import OllamaChatModel
from agentscope.model import FinishedReason


class _Body(httpx.AsyncByteStream):
    """A provider response with observable, optionally delayed cleanup."""

    def __init__(self, blocked: bool = False, broken: bool = False) -> None:
        self.closed = False
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.release_close.set()
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        self.blocked = blocked
        self.broken = broken

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield json.dumps(
            {
                "model": "offline",
                "message": {"role": "assistant", "content": "hello"},
                "done": False,
            },
        ).encode() + b"\n"
        self.read_started.set()
        if self.blocked:
            await self.release_read.wait()
        if self.broken:
            raise httpx.ReadError("offline transport failed")
        yield json.dumps(
            {
                "model": "offline",
                "message": {"role": "assistant", "content": " world"},
                "done": True,
                "prompt_eval_count": 1,
                "eval_count": 2,
            },
        ).encode() + b"\n"

    async def aclose(self) -> None:
        """Finish only after the test releases transport cleanup."""
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


def _model(body: _Body) -> OllamaChatModel:
    """Use the real Ollama client with an in-process external transport."""
    return OllamaChatModel(
        credential=OllamaCredential(host="http://offline.test"),
        model="offline",
        client_kwargs={
            "transport": httpx.MockTransport(
                lambda request: httpx.Response(200, stream=body),
            ),
        },
    )


@skipUnless(find_spec("ollama"), "Ollama extra is not installed")
class OllamaStreamCloseTest(IsolatedAsyncioTestCase):
    """Closing a public model stream awaits the SDK response cleanup."""

    async def test_early_close_releases_http_body(self) -> None:
        """A suspended SDK stream is closed before aclose returns."""
        body = _Body()
        stream = await _model(body)([])
        try:
            self.assertEqual((await anext(stream)).content[0].text, "hello")
            self.assertFalse(body.closed)
            await stream.aclose()
            self.assertTrue(body.closed)
        finally:
            await stream.aclose()
            await body.aclose()

    async def test_early_close_waits_for_async_cleanup(self) -> None:
        """The close call cannot finish before transport cleanup finishes."""
        body = _Body()
        body.release_close.clear()
        stream = await _model(body)([])
        await anext(stream)
        closing = asyncio.create_task(stream.aclose())
        try:
            await asyncio.wait_for(body.close_started.wait(), timeout=1)
            self.assertFalse(closing.done())
            self.assertFalse(body.closed)
            body.release_close.set()
            await asyncio.wait_for(closing, timeout=1)
            self.assertTrue(body.closed)
        finally:
            body.release_close.set()
            await closing
            await stream.aclose()
            await body.aclose()

    async def test_normal_completion_preserves_accumulation(self) -> None:
        """Natural exhaustion still closes and returns the accumulated text."""
        body = _Body()
        chunks = [chunk async for chunk in await _model(body)([])]
        self.assertTrue(body.closed)
        self.assertTrue(chunks[-1].is_last)
        self.assertEqual(chunks[-1].content[0].text, "hello world")

    async def test_transport_error_propagates_and_closes(self) -> None:
        """A read failure keeps its original exception and closes the body."""
        body = _Body(broken=True)
        stream = await _model(body)([])
        try:
            await anext(stream)
            with self.assertRaisesRegex(httpx.ReadError, "offline"):
                await anext(stream)
            self.assertTrue(body.closed)
        finally:
            await stream.aclose()
            await body.aclose()

    async def test_cancelled_read_keeps_interrupted_result(self) -> None:
        """Cancellation retains the base wrapper's interrupted response."""
        body = _Body(blocked=True)
        stream = await _model(body)([])
        try:
            await anext(stream)
            reading = asyncio.create_task(anext(stream))
            await asyncio.wait_for(body.read_started.wait(), timeout=1)
            reading.cancel()
            final = await reading
            self.assertEqual(final.finished_reason, FinishedReason.INTERRUPTED)
            self.assertTrue(body.closed)
        finally:
            await stream.aclose()
            await body.aclose()
