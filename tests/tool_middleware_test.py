# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test cases for the tool-level onion middleware mechanism."""
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.tool import (
    ToolBase,
    ToolMiddlewareBase,
    ToolChunk,
)
from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
)


def _expected_chunk(text: str) -> dict:
    """Build the full expected ``ToolChunk.model_dump()`` dict for the given
    text, with random ``id`` fields matched by :class:`AnyString`."""
    return {
        "content": [{"type": "text", "text": text, "id": AnyString()}],
        "state": ToolResultState.RUNNING,
        "is_last": True,
        "metadata": {},
        "id": AnyString(),
    }


class _NonStreamingTool(ToolBase):
    """A tool whose ``call`` returns a single ToolChunk (coroutine)."""

    name: str = "non_streaming_tool"
    description: str = "A tool that returns a single chunk."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Run the tool, echoing back the received kwargs."""
        return ToolChunk(
            content=[TextBlock(text=f"call() kwargs={kwargs}")],
        )


class _StreamingTool(ToolBase):
    """A tool whose ``call`` is an async generator function."""

    name: str = "streaming_tool"
    description: str = "A tool that yields several chunks."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(
        self,
        n: int = 2,
        **kwargs: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Yield ``n`` chunks."""
        for i in range(n):
            yield ToolChunk(content=[TextBlock(text=f"chunk-{i}")])


class _NoSuperInitTool(ToolBase):
    """A tool that overrides ``__init__`` without calling ``super().__init__``.

    Used to verify the no-middleware path still works (via the ``getattr``
    fallback for ``_middlewares``).
    """

    name: str = "no_super_init_tool"
    description: str = "A tool that skips super().__init__()."
    input_schema: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    def __init__(self) -> None:  # pylint: disable=super-init-not-called
        """Intentionally does not call ``super().__init__()``."""
        self.marker = "initialized"

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Run the tool."""
        return ToolChunk(content=[TextBlock(text="no super init")])


def _make_recording_middleware(
    label: str,
    execution_order: list[str],
) -> ToolMiddlewareBase:
    """Build a middleware that records pre/post markers in ``execution_order``
    and transparently forwards the chunks."""

    class _Middleware(ToolMiddlewareBase):
        async def on_tool_call(
            self,
            tool: Any,
            input_kwargs: dict,
            next_handler: Any,
        ) -> AsyncGenerator:
            execution_order.append(f"{label}-pre")
            async for chunk in next_handler(**input_kwargs):
                yield chunk
            execution_order.append(f"{label}-post")

    return _Middleware()


class ToolMiddlewareTest(IsolatedAsyncioTestCase):
    """Tests for the tool-level onion middleware mechanism."""

    async def _drain(self, result: Any) -> list[dict]:
        """Collect all chunks from a middleware-wrapped ``__call__`` result,
        returning their full ``model_dump()`` dicts for whole-object asserts.
        """
        chunks = []
        async for chunk in result:
            chunks.append(chunk.model_dump())
        return chunks

    async def test_no_middleware_non_streaming(self) -> None:
        """A non-streaming tool with no middleware returns a single chunk."""
        tool = _NonStreamingTool()
        result = await tool()
        self.assertIsInstance(result, ToolChunk)
        self.assertEqual(
            result.model_dump(),
            _expected_chunk("call() kwargs={}"),
        )

    async def test_no_middleware_streaming(self) -> None:
        """A streaming tool with no middleware yields its chunks directly."""
        tool = _StreamingTool()
        result = await tool(n=3)
        chunks = await self._drain(result)
        self.assertEqual(
            chunks,
            [
                _expected_chunk("chunk-0"),
                _expected_chunk("chunk-1"),
                _expected_chunk("chunk-2"),
            ],
        )

    async def test_middleware_wraps_call_in_onion_order(self) -> None:
        """Two middlewares wrap call() in the correct onion order.

        Execution order must be:
        outer-pre -> inner-pre -> call() -> inner-post -> outer-post.
        """
        execution_order: list[str] = []
        tool = _NonStreamingTool(
            middlewares=[
                _make_recording_middleware("outer", execution_order),
                _make_recording_middleware("inner", execution_order),
            ],
        )

        chunks = await self._drain(await tool())

        self.assertEqual(chunks, [_expected_chunk("call() kwargs={}")])
        self.assertEqual(
            execution_order,
            ["outer-pre", "inner-pre", "inner-post", "outer-post"],
        )

    async def test_single_middleware(self) -> None:
        """A single middleware fires pre and post around call()."""
        execution_order: list[str] = []
        tool = _NonStreamingTool(
            middlewares=[_make_recording_middleware("only", execution_order)],
        )

        chunks = await self._drain(await tool())

        self.assertEqual(chunks, [_expected_chunk("call() kwargs={}")])
        self.assertEqual(execution_order, ["only-pre", "only-post"])

    async def test_middleware_with_streaming_tool(self) -> None:
        """Middleware transparently forwards each chunk of a streaming tool."""
        execution_order: list[str] = []
        tool = _StreamingTool(
            middlewares=[_make_recording_middleware("mw", execution_order)],
        )

        chunks = await self._drain(await tool(n=3))

        self.assertEqual(
            chunks,
            [
                _expected_chunk("chunk-0"),
                _expected_chunk("chunk-1"),
                _expected_chunk("chunk-2"),
            ],
        )
        self.assertEqual(execution_order, ["mw-pre", "mw-post"])

    async def test_middleware_can_rewrite_input_kwargs(self) -> None:
        """A middleware can mutate input_kwargs so the tool sees new args."""

        class _RewriteMiddleware(ToolMiddlewareBase):
            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: dict,
                next_handler: Any,
            ) -> AsyncGenerator:
                input_kwargs["injected"] = "value"
                async for chunk in next_handler(**input_kwargs):
                    yield chunk

        tool = _NonStreamingTool(middlewares=[_RewriteMiddleware()])
        chunks = await self._drain(await tool(original="x"))

        self.assertEqual(
            chunks,
            [
                _expected_chunk(
                    "call() kwargs={'original': 'x', 'injected': 'value'}",
                ),
            ],
        )

    async def test_middleware_can_transform_chunks(self) -> None:
        """A middleware can transform the chunks yielded by the tool."""

        class _UppercaseMiddleware(ToolMiddlewareBase):
            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: dict,
                next_handler: Any,
            ) -> AsyncGenerator:
                async for chunk in next_handler(**input_kwargs):
                    yield ToolChunk(
                        content=[
                            TextBlock(text=chunk.content[0].text.upper()),
                        ],
                    )

        tool = _StreamingTool(middlewares=[_UppercaseMiddleware()])
        chunks = await self._drain(await tool(n=2))

        self.assertEqual(
            chunks,
            [_expected_chunk("CHUNK-0"), _expected_chunk("CHUNK-1")],
        )

    async def test_middleware_can_short_circuit(self) -> None:
        """A middleware that never calls next_handler skips the tool."""
        called = {"value": False}

        class _ShortCircuitTool(_NonStreamingTool):
            async def call(self, **kwargs: Any) -> ToolChunk:
                called["value"] = True
                return await super().call(**kwargs)

        class _ShortCircuitMiddleware(ToolMiddlewareBase):
            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: dict,
                next_handler: Any,
            ) -> AsyncGenerator:
                yield ToolChunk(content=[TextBlock(text="short-circuited")])

        tool = _ShortCircuitTool(middlewares=[_ShortCircuitMiddleware()])
        chunks = await self._drain(await tool())

        self.assertFalse(called["value"])
        self.assertEqual(chunks, [_expected_chunk("short-circuited")])

    async def test_middleware_exception_propagates(self) -> None:
        """An exception raised in a middleware propagates to the caller."""

        class _BoomMiddleware(ToolMiddlewareBase):
            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: dict,
                next_handler: Any,
            ) -> AsyncGenerator:
                raise ValueError("boom")
                yield  # pylint: disable=unreachable

        tool = _NonStreamingTool(middlewares=[_BoomMiddleware()])

        with self.assertRaises(ValueError):
            await self._drain(await tool())

    async def test_positional_args_rejected(self) -> None:
        """Calling a tool with positional args raises TypeError instead of
        silently dropping them."""
        tool = _NonStreamingTool()
        with self.assertRaises(TypeError):
            await tool("positional")  # type: ignore[call-arg]

    async def test_missing_super_init_no_middleware(self) -> None:
        """A tool that skips super().__init__() still works on the
        no-middleware path via the getattr fallback for _middlewares."""
        tool = _NoSuperInitTool()
        self.assertFalse(hasattr(tool, "_middlewares"))
        result = await tool()
        self.assertIsInstance(result, ToolChunk)
        self.assertEqual(result.model_dump(), _expected_chunk("no super init"))
