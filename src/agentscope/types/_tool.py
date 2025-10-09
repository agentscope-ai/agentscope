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
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    from ..tool import ToolResponse
else:
    ToolResponse = "ToolResponse"

ToolFunction = Callable[
    ...,
    Union[
        # sync function
        ToolResponse,
        # async function
        Awaitable[ToolResponse],
        # sync generator function
        Generator[ToolResponse, None, None],
        # async generator function
        AsyncGenerator[ToolResponse, None],
        # async function that returns async generator
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
        # async function that returns sync generator
        Coroutine[Any, Any, Generator[ToolResponse, None, None]],
    ],
]
