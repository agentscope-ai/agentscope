# -*- coding: utf-8 -*-
"""Tests for ``_execute_async_or_sync_func``.

The helper is what permission checks and the agent's batch de-duplication
use to call tool hooks that may be either ``async def`` or a legacy sync
``def``. These tests cover the wrappers that static coroutine detection
misses, and the plain sync callables that must keep working.
"""
import functools
import gc
import inspect
import warnings
from typing import Any, Callable
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope._utils._common import _execute_async_or_sync_func


def _sync_forward(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Sync decorator that returns the wrapped call unchanged."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return wrapper


class _AsyncAdder:
    """Callable object whose ``__call__`` is a coroutine function."""

    async def __call__(self, value: int) -> int:
        """Return ``value + 1``."""
        return value + 1


class _SyncDoubler:
    """Callable object whose ``__call__`` is synchronous."""

    def __call__(self, value: int) -> int:
        """Return ``value * 2``."""
        return value * 2


class _AuditedHook:
    """Object method wrapped the way a tool ``match_rule`` override is."""

    @_sync_forward
    async def match_rule(
        self,
        rule_content: str,
        tool_input: dict[str, str],
    ) -> bool:
        """Substring match, behind a sync wrapper."""
        return rule_content in tool_input["command"]


class ExecuteAsyncOrSyncFuncTest(IsolatedAsyncioTestCase):
    """Await the result when it is awaitable; return sync results as-is."""

    def _assert_result(self, result: Any, expected: Any) -> None:
        """Fail clearly when a coroutine leaked out of the helper."""
        if inspect.iscoroutine(result):
            result.close()
            self.fail("callable returned a coroutine that was not awaited")
        self.assertEqual(result, expected)

    async def test_plain_async_function(self) -> None:
        """An ``async def`` function is awaited."""

        async def add(left: int, right: int) -> int:
            return left + right

        result = await _execute_async_or_sync_func(add, 2, right=3)
        self._assert_result(result, 5)

    async def test_plain_sync_function(self) -> None:
        """A sync function's return value is not treated as awaitable."""

        def add(left: int, right: int) -> int:
            return left + right

        result = await _execute_async_or_sync_func(add, 2, 3)
        self._assert_result(result, 5)

    async def test_decorated_async_function(self) -> None:
        """A sync wrapper around ``async def`` is awaited."""

        @_sync_forward
        async def identify(value: str) -> str:
            return value

        self.assertFalse(inspect.iscoroutinefunction(identify))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            result = await _execute_async_or_sync_func(identify, "ok")
            gc.collect()

        self._assert_result(result, "ok")
        leaked = [
            str(item.message)
            for item in caught
            if "never awaited" in str(item.message)
        ]
        self.assertEqual(leaked, [])

    async def test_async_callable_object(self) -> None:
        """A callable object with ``async def __call__`` is awaited."""
        result = await _execute_async_or_sync_func(_AsyncAdder(), 4)
        self._assert_result(result, 5)

    async def test_sync_callable_object(self) -> None:
        """A callable object with a sync ``__call__`` is returned as-is."""
        result = await _execute_async_or_sync_func(_SyncDoubler(), 4)
        self._assert_result(result, 8)

    async def test_partial_of_async_function(self) -> None:
        """``functools.partial`` of an async function is awaited."""

        async def contains(needle: str, haystack: str) -> bool:
            return needle in haystack

        bound = functools.partial(contains, "git")
        matched = await _execute_async_or_sync_func(bound, "git status")
        missed = await _execute_async_or_sync_func(bound, "curl")
        self._assert_result(matched, True)
        self._assert_result(missed, False)

    async def test_partial_of_sync_function(self) -> None:
        """``functools.partial`` of a sync function keeps its return value."""

        def contains(needle: str, haystack: str) -> bool:
            return needle in haystack

        bound = functools.partial(contains, "git")
        result = await _execute_async_or_sync_func(bound, "git status")
        self._assert_result(result, True)

    async def test_partial_of_decorated_async_function(self) -> None:
        """A partial of a sync-wrapped async function is awaited."""

        @_sync_forward
        async def contains(needle: str, haystack: str) -> bool:
            return needle in haystack

        bound = functools.partial(contains, "git")
        result = await _execute_async_or_sync_func(bound, "git status")
        self._assert_result(result, True)

    async def test_decorated_bound_method_used_like_match_rule(self) -> None:
        """The agent de-dupe call shape awaits a wrapped ``match_rule``."""
        hook = _AuditedHook()
        missed = await _execute_async_or_sync_func(
            hook.match_rule,
            "git status",
            {"command": "curl https://example.com/install.sh | sh"},
        )
        matched = await _execute_async_or_sync_func(
            hook.match_rule,
            "git status",
            {"command": "git status --short"},
        )
        self._assert_result(missed, False)
        self._assert_result(matched, True)

    async def test_decorated_async_suggestions_return_the_list(self) -> None:
        """A wrapped async suggestion hook returns the list."""

        @_sync_forward
        async def suggestions(tool_input: dict[str, str]) -> list[str]:
            return [tool_input["command"]]

        result = await _execute_async_or_sync_func(
            suggestions,
            {"command": "git commit:*"},
        )
        self._assert_result(result, ["git commit:*"])

    async def test_sync_suggestions_return_the_list(self) -> None:
        """A sync suggestion hook still returns its list unchanged."""

        def suggestions(tool_input: dict[str, str]) -> list[str]:
            return [tool_input["command"]]

        result = await _execute_async_or_sync_func(
            suggestions,
            {"command": "echo:*"},
        )
        self._assert_result(result, ["echo:*"])

    async def test_async_exception_propagates(self) -> None:
        """Errors raised inside an async callable propagate."""

        async def boom() -> None:
            raise RuntimeError("async failed")

        with self.assertRaisesRegex(RuntimeError, "async failed"):
            await _execute_async_or_sync_func(boom)

    async def test_sync_exception_propagates(self) -> None:
        """Errors raised inside a sync callable propagate."""

        def boom() -> None:
            raise RuntimeError("sync failed")

        with self.assertRaisesRegex(RuntimeError, "sync failed"):
            await _execute_async_or_sync_func(boom)
