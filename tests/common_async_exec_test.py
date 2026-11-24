# -*- coding: utf-8 -*-
"""Tests for callables whose return values must be awaited."""
import functools
from collections.abc import Awaitable
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope._utils._common import _execute_async_or_sync_func


class _AsyncAdder:
    """Callable object whose ``__call__`` is a coroutine function."""

    async def __call__(self, value: int) -> int:
        """Return ``value + 1``."""
        return value + 1


class ExecuteAsyncOrSyncFuncTest(IsolatedAsyncioTestCase):
    """Cover async results from callables with synchronous interfaces."""

    async def test_decorated_async_function(self) -> None:
        """A synchronous wrapper preserves the awaited False result."""

        async def matches() -> bool:
            return False

        @functools.wraps(matches)
        def wrapper() -> Awaitable[bool]:
            return matches()

        result = await _execute_async_or_sync_func(wrapper)
        self.assertIs(result, False)

    async def test_async_callable_object(self) -> None:
        """A callable object with async __call__ is awaited."""
        result = await _execute_async_or_sync_func(_AsyncAdder(), 4)
        self.assertEqual(result, 5)
