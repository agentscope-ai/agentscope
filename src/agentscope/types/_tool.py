# -*- coding: utf-8 -*-
"""The tool related types"""

from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
)
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..tool import ToolResponse

    ToolFunction = Callable[
        ...,
        # sync function
        ToolResponse
        # async function
        | Awaitable[ToolResponse]
        # sync generator function
        | Generator[ToolResponse, None, None]
        # async generator function
        | AsyncGenerator[ToolResponse, None]
        # async function that returns async generator
        | Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]]
        # async function that returns sync generator
        | Coroutine[Any, Any, Generator[ToolResponse, None, None]],
    ]
else:
    ToolFunction = Callable
