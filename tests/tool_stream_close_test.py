# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Explicit tool-stream closure must finish owned resource cleanup."""
import asyncio
from typing import Any, AsyncGenerator, Callable, Generator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    ToolMiddlewareBase,
)


class _Forward(ToolMiddlewareBase):
    """Forward chunks without taking ownership of the next handler."""

    def __init__(self, label: str, closed: list[str]) -> None:
        self.label = label
        self.closed = closed

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Record awaited cleanup while forwarding the tool's output."""
        try:
            async for chunk in next_handler(**input_kwargs):
                yield chunk
        finally:
            await asyncio.sleep(0)
            self.closed.append(self.label)


class _NativeStream(ToolBase):
    """Exercise a call method that is an async-generator function."""

    name = "native_stream"
    description = "An offline streaming tool."
    input_schema: dict = {"type": "object", "properties": {}}

    def __init__(self, closed: list[str]) -> None:
        super().__init__(middlewares=[_Forward("middleware", closed)])
        self.closed = closed

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Allow this local test tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, value: str) -> AsyncGenerator[ToolChunk, None]:
        """Yield two chunks with asynchronous resource cleanup."""
        try:
            yield ToolChunk(content=[TextBlock(text=value)])
            yield ToolChunk(content=[TextBlock(text="second")])
        finally:
            await asyncio.sleep(0)
            self.closed.append("tool")


class ToolStreamCloseTest(IsolatedAsyncioTestCase):
    """Close must reach the source without relying on garbage collection."""

    async def _check_async_close(self, wrapped: bool) -> None:
        """Check one invocation without sharing loop-bound closures."""
        closed: list[str] = []

        async def produce() -> AsyncGenerator[str, None]:
            try:
                yield "first"
                yield "second"
            finally:
                await asyncio.sleep(0)
                closed.append("tool")

        source = produce()
        wrappers = [_Forward("outer", closed), _Forward("inner", closed)]
        tool = FunctionTool(
            lambda: source,
            name="streaming_tool",
            middlewares=wrappers if wrapped else None,
        )
        stream = await tool()
        try:
            self.assertEqual((await anext(stream)).content[0].text, "first")
            await stream.aclose()
            self.assertEqual(
                closed,
                ["tool", "inner", "outer"] if wrapped else ["tool"],
            )
        finally:
            await stream.aclose()
            await source.aclose()

    async def test_async_generator_close_awaits_owned_cleanup(self) -> None:
        """A retained source and nested middleware finish before close."""
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                await self._check_async_close(wrapped)

    async def _check_sync_close(self, wrapped: bool) -> None:
        """Check one invocation without sharing loop-bound closures."""
        closed: list[str] = []

        def produce() -> Generator[str, None, None]:
            try:
                yield "first"
                yield "second"
            finally:
                closed.append("tool")

        source = produce()
        tool = FunctionTool(
            lambda: source,
            name="streaming_tool",
            middlewares=[_Forward("middleware", closed)] if wrapped else None,
        )
        stream = await tool()
        try:
            await anext(stream)
            await stream.aclose()
            self.assertEqual(
                closed,
                ["tool", "middleware"] if wrapped else ["tool"],
            )
        finally:
            await stream.aclose()
            source.close()

    async def test_sync_generator_close_reaches_owned_source(self) -> None:
        """The async adapter also closes a retained sync generator."""
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                await self._check_sync_close(wrapped)

    async def test_native_stream_closes_through_middleware(self) -> None:
        """Native async-generator call methods share the close contract."""
        closed: list[str] = []
        stream = await _NativeStream(closed)(value="forwarded")
        try:
            self.assertEqual(
                (await anext(stream)).content[0].text,
                "forwarded",
            )
            await stream.aclose()
            self.assertEqual(closed, ["tool", "middleware"])
        finally:
            await stream.aclose()

    async def test_cleanup_error_still_closes_middleware(self) -> None:
        """A source cleanup error propagates after other owners close."""
        closed: list[str] = []

        async def produce() -> AsyncGenerator[str, None]:
            try:
                yield "first"
            finally:
                await asyncio.sleep(0)
                raise ValueError("cleanup failed")

        source = produce()
        tool = FunctionTool(
            lambda: source,
            name="streaming_tool",
            middlewares=[_Forward("middleware", closed)],
        )
        stream = await tool()
        try:
            await anext(stream)
            with self.assertRaisesRegex(ValueError, "cleanup failed"):
                await stream.aclose()
            self.assertEqual(closed, ["middleware"])
        finally:
            await stream.aclose()
            if source.ag_frame is not None:
                with self.assertRaisesRegex(ValueError, "cleanup failed"):
                    await source.aclose()

    async def _check_consumption(self, fail: bool) -> None:
        """Check normal completion and ordinary source errors."""
        closed: list[str] = []

        async def produce() -> AsyncGenerator[str, None]:
            try:
                yield "first"
                if fail:
                    raise ValueError("tool failed")
                yield "second"
            finally:
                closed.append("tool")

        tool = FunctionTool(
            produce,
            middlewares=[_Forward("middleware", closed)],
        )
        stream = await tool()
        values = []
        try:
            if fail:
                with self.assertRaisesRegex(ValueError, "tool failed"):
                    async for chunk in stream:
                        values.append(chunk.content[0].text)
            else:
                async for chunk in stream:
                    values.append(chunk.content[0].text)
            self.assertEqual(
                values, ["first"] if fail else ["first", "second"]
            )
            self.assertEqual(closed, ["tool", "middleware"])
        finally:
            await stream.aclose()

    async def test_full_consumption_and_errors_preserve_cleanup(self) -> None:
        """Ordinary iteration keeps values and propagates source errors."""
        for fail in (False, True):
            with self.subTest(fail=fail):
                await self._check_consumption(fail)

    async def test_cancellation_finishes_owned_cleanup(self) -> None:
        """Cancelling a pending pull preserves cancellation and cleanup."""
        entered = asyncio.Event()
        closed: list[str] = []

        async def produce() -> AsyncGenerator[str, None]:
            try:
                yield "first"
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                closed.append("tool")

        stream = await FunctionTool(
            produce,
            middlewares=[_Forward("middleware", closed)],
        )()
        await anext(stream)
        pull = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            pull.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pull
            self.assertEqual(closed, ["tool", "middleware"])
        finally:
            pull.cancel()
            await asyncio.gather(pull, return_exceptions=True)
            await stream.aclose()

    async def test_closing_one_invocation_leaves_another_open(self) -> None:
        """Per-call stream ownership cannot close a sibling invocation."""
        closed: list[str] = []
        sources = []

        async def produce(value: str) -> AsyncGenerator[str, None]:
            try:
                yield value
                yield value + "-second"
            finally:
                closed.append(value)

        def create(value: str) -> AsyncGenerator[str, None]:
            source = produce(value)
            sources.append(source)
            return source

        tool = FunctionTool(create, middlewares=[_Forward("mw", [])])
        first = await tool(value="first")
        second = await tool(value="second")
        try:
            await anext(first)
            await anext(second)
            await first.aclose()
            self.assertEqual(closed, ["first"])
            self.assertEqual(
                (await anext(second)).content[0].text,
                "second-second",
            )
        finally:
            await first.aclose()
            await second.aclose()
            for source in sources:
                await source.aclose()
