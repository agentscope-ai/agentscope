# -*- coding: utf-8 -*-
"""Coordinate an AUTO tool invocation with its result event stream."""
import asyncio
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from typing import AsyncGenerator, AsyncIterator

from ..exception import AgentOrientedException
from ..tool import ToolChunk, ToolResponse


_dispatch_ready: ContextVar[asyncio.Future | None] = ContextVar(
    "tool_dispatch_ready",
    default=None,
)


async def wait_for_tool_dispatch(task: asyncio.Task) -> None:
    """Start the background execution timeout only after tool dispatch."""
    ready = _dispatch_ready.get()
    if ready is not None:
        try:
            await asyncio.wait(
                (ready, task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise


@asynccontextmanager
async def dispatch_stream(
    stream: AsyncGenerator[ToolChunk | ToolResponse, None],
    ready: asyncio.Future,
) -> AsyncIterator[AsyncIterator[ToolChunk | ToolResponse]]:
    """Run acting until dispatch and stream its results with backpressure."""
    queue: asyncio.Queue = asyncio.Queue()
    slot = asyncio.Semaphore(1)
    closing = False
    buffered = []
    end = object()

    async def produce() -> None:
        token = _dispatch_ready.set(ready)
        try:
            async with aclosing(stream):
                async for item in stream:
                    if closing:
                        continue
                    if ready.done():
                        await slot.acquire()
                        queue.put_nowait(item)
                    else:
                        buffered.append(item)
        except AgentOrientedException as exc:
            if ready.done():
                raise
            buffered.clear()
            ready.set_exception(exc)
        except BaseException:
            if not ready.done():
                buffered.clear()
            raise
        finally:
            _dispatch_ready.reset(token)
            if not ready.done():
                # Middleware can supply a result without invoking a tool.
                ready.set_result(None)
            queue.put_nowait(end)

    async def chunks() -> AsyncIterator[ToolChunk | ToolResponse]:
        for item in buffered:
            yield item
        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                # The toolkit emits an interrupted result on cancellation.
                task.cancel()
                continue
            if item is end:
                break
            slot.release()
            yield item
        await task

    task = asyncio.create_task(produce())
    try:
        await ready
        yield chunks()
    finally:
        closing = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
