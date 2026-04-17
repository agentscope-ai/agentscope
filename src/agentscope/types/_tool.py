# -*- coding: utf-8 -*-
"""The tool related types"""

from typing import (
    Callable,
    Union,
    Awaitable,
    AsyncGenerator,
    Generator,
    Coroutine,
    Any,
    TypeAlias,
    Literal,
)

from ..tool import ToolChunk

ToolFunction: TypeAlias = Callable[
    ...,
    Union[
        # sync function
        ToolChunk,
        # async function
        Awaitable[ToolChunk],
        # sync generator function
        Generator[ToolChunk, None, None],
        # async generator function
        AsyncGenerator[ToolChunk, None],
        # async function that returns async generator
        Coroutine[Any, Any, AsyncGenerator[ToolChunk, None]],
        # async function that returns sync generator
        Coroutine[Any, Any, Generator[ToolChunk, None, None]],
    ],
]


ToolChoice: TypeAlias = Literal["auto", "none", "required"] | str
