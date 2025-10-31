# -*- coding: utf-8 -*-
"""The functions that wrap object, sync generator, and async generator
into async generators.

TODO: handle the exception raised when yielding from async generator
 into a normal ToolResponse instance.
"""
import asyncio
from typing import AsyncGenerator, Generator, Callable, Awaitable

from ._response import ToolResponse
from ..message import TextBlock
from .._utils._common import _execute_async_or_sync_func


async def _postprocess_tool_response(
    tool_response: ToolResponse,
    postprocess_func: (
        Callable[[ToolResponse], ToolResponse | None]
        | Callable[[ToolResponse], Awaitable[ToolResponse | None]]
    )
    | None,
) -> ToolResponse:
    """Post-process a ToolResponse object with the given function.

    Supports both sync and async postprocess_func.
    """
    if postprocess_func:
        processed_response = await _execute_async_or_sync_func(
            postprocess_func,
            tool_response,
        )
        if processed_response:
            return processed_response
    return tool_response


async def _object_wrapper(
    obj: ToolResponse,
    postprocess_func: (
        Callable[[ToolResponse], ToolResponse | None]
        | Callable[[ToolResponse], Awaitable[ToolResponse | None]]
    )
    | None,
) -> AsyncGenerator[ToolResponse, None]:
    """Wrap a ToolResponse object to an async generator."""
    yield await _postprocess_tool_response(obj, postprocess_func)


async def _sync_generator_wrapper(
    sync_generator: Generator[ToolResponse, None, None],
    postprocess_func: (
        Callable[[ToolResponse], ToolResponse | None]
        | Callable[[ToolResponse], Awaitable[ToolResponse | None]]
    )
    | None,
) -> AsyncGenerator[ToolResponse, None]:
    """Wrap a sync generator to an async generator."""
    for chunk in sync_generator:
        yield await _postprocess_tool_response(chunk, postprocess_func)


async def _async_generator_wrapper(
    async_func: AsyncGenerator[ToolResponse, None],
    postprocess_func: (
        Callable[[ToolResponse], ToolResponse | None]
        | Callable[[ToolResponse], Awaitable[ToolResponse | None]]
    )
    | None,
) -> AsyncGenerator[ToolResponse, None]:
    """When the function is interrupted during generating the tool
    response, add an interrupted message to the response, and postpone
    the CancelledError to the caller."""

    last_chunk = None
    try:
        async for chunk in async_func:
            processed_chunk = await _postprocess_tool_response(
                chunk,
                postprocess_func,
            )
            yield processed_chunk
            last_chunk = processed_chunk

    except asyncio.CancelledError:
        interrupted_info = TextBlock(
            type="text",
            text="<system-info>"
            "The tool call has been interrupted by the user."
            "</system-info>",
        )
        if last_chunk:
            last_chunk.content.append(interrupted_info)
            last_chunk.is_interrupted = True
            last_chunk.is_last = True
            yield await _postprocess_tool_response(
                last_chunk,
                postprocess_func,
            )

        else:
            yield await _postprocess_tool_response(
                ToolResponse(
                    content=[interrupted_info],
                    is_interrupted=True,
                    is_last=True,
                ),
                postprocess_func,
            )
