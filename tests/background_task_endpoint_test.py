# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the background task endpoint feature.

Tests cover:
- TaskStatus lifecycle transitions in BackgroundTaskManager
- Capacity-based eviction of terminal-state tasks
- _make_summary helper in ToolOffloadMiddleware
- Background task REST endpoints (list + detail)
"""
import asyncio
import json
import time
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.app._manager import BackgroundTaskManager, TaskStatus
from agentscope.app._manager._background_task_manager import (
    _MAX_RETAINED_TASKS,
)
from agentscope.app.message_bus import MessageBus
from agentscope.app.middleware._tool_offload_middleware import (
    _make_summary,
)


class TestTaskStatusLifecycle(IsolatedAsyncioTestCase):
    """Test status transitions in BackgroundTaskManager._on_done."""

    async def asyncSetUp(self) -> None:
        """Set up a manager with a mock message bus."""
        self.bus = MagicMock(spec=MessageBus)
        self.bus.bg_task_register = AsyncMock()
        self.bus.bg_task_unregister = AsyncMock()
        self.mgr = BackgroundTaskManager(message_bus=self.bus)

    async def test_completed_status_on_normal_finish(self) -> None:
        """A task that completes normally transitions to COMPLETED."""

        async def _work() -> str:
            return "done"

        task = asyncio.create_task(_work())
        task_id = await self.mgr.register_task(
            asyncio_task=task,
            session_id="s1",
            agent_id="a1",
            user_id="u1",
            tool_name="test_tool",
            summary="test_tool()",
        )
        await asyncio.sleep(0.05)

        bg = self.mgr.tasks[task_id]
        self.assertEqual(bg.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(bg.completed_at)
        self.assertIsNone(bg.error_summary)

    async def test_failed_status_on_exception(self) -> None:
        """A task that raises transitions to FAILED with error_summary."""

        async def _work() -> None:
            raise ValueError("something broke")

        task = asyncio.create_task(_work())
        task_id = await self.mgr.register_task(
            asyncio_task=task,
            session_id="s1",
            agent_id="a1",
            user_id="u1",
            tool_name="broken_tool",
        )
        await asyncio.sleep(0.05)

        bg = self.mgr.tasks[task_id]
        self.assertEqual(bg.status, TaskStatus.FAILED)
        self.assertIn("something broke", bg.error_summary or "")
        self.assertIsNotNone(bg.completed_at)

    async def test_cancelled_status_on_cancel(self) -> None:
        """A cancelled task transitions to CANCELLED."""

        async def _work() -> None:
            await asyncio.sleep(100)

        task = asyncio.create_task(_work())
        task_id = await self.mgr.register_task(
            asyncio_task=task,
            session_id="s1",
            agent_id="a1",
            user_id="u1",
            tool_name="long_tool",
        )
        task.cancel()
        await asyncio.sleep(0.05)

        bg = self.mgr.tasks[task_id]
        self.assertEqual(bg.status, TaskStatus.CANCELLED)
        self.assertIsNotNone(bg.completed_at)

    async def test_summary_and_started_at_are_recorded(self) -> None:
        """Registered tasks store summary and started_at."""

        async def _work() -> None:
            await asyncio.sleep(100)

        before = time.time()
        task = asyncio.create_task(_work())
        task_id = await self.mgr.register_task(
            asyncio_task=task,
            session_id="s1",
            agent_id="a1",
            user_id="u1",
            tool_name="my_tool",
            summary="my_tool(x=1)",
        )
        after = time.time()

        bg = self.mgr.tasks[task_id]
        self.assertEqual(bg.summary, "my_tool(x=1)")
        self.assertGreaterEqual(bg.started_at, before)
        self.assertLessEqual(bg.started_at, after)

        task.cancel()
        await asyncio.sleep(0.05)


class TestEviction(IsolatedAsyncioTestCase):
    """Test capacity-based eviction of terminal-state tasks."""

    async def asyncSetUp(self) -> None:
        """Set up a manager with a mock message bus."""
        self.bus = MagicMock(spec=MessageBus)
        self.bus.bg_task_register = AsyncMock()
        self.bus.bg_task_unregister = AsyncMock()
        self.mgr = BackgroundTaskManager(message_bus=self.bus)

    async def test_eviction_removes_oldest_completed(self) -> None:
        """When completed tasks exceed _MAX_RETAINED_TASKS, oldest are
        removed."""
        tasks = []
        for i in range(_MAX_RETAINED_TASKS + 5):

            async def _work(idx: int = i) -> int:
                return idx

            t = asyncio.create_task(_work())
            await self.mgr.register_task(
                asyncio_task=t,
                session_id="s",
                agent_id="a",
                user_id="u",
                tool_name=f"tool_{i}",
            )
            tasks.append(t)

        # Wait for all to complete
        await asyncio.sleep(0.1)

        completed = [
            t
            for t in self.mgr.tasks.values()
            if t.status != TaskStatus.RUNNING
        ]
        self.assertLessEqual(len(completed), _MAX_RETAINED_TASKS)


class TestListAndGetTasks(IsolatedAsyncioTestCase):
    """Test list_tasks and get_task query methods."""

    async def asyncSetUp(self) -> None:
        """Set up a manager with a mock message bus."""
        self.bus = MagicMock(spec=MessageBus)
        self.bus.bg_task_register = AsyncMock()
        self.bus.bg_task_unregister = AsyncMock()
        self.mgr = BackgroundTaskManager(message_bus=self.bus)

    async def test_list_tasks_filters_by_user(self) -> None:
        """list_tasks only returns tasks owned by the specified user."""

        async def _work() -> None:
            await asyncio.sleep(100)

        t1 = asyncio.create_task(_work())
        t2 = asyncio.create_task(_work())
        await self.mgr.register_task(
            asyncio_task=t1,
            session_id="s1",
            agent_id="a1",
            user_id="alice",
            tool_name="tool_a",
        )
        await self.mgr.register_task(
            asyncio_task=t2,
            session_id="s2",
            agent_id="a2",
            user_id="bob",
            tool_name="tool_b",
        )

        alice_tasks = self.mgr.list_tasks(user_id="alice")
        self.assertEqual(len(alice_tasks), 1)
        self.assertEqual(alice_tasks[0].user_id, "alice")

        bob_tasks = self.mgr.list_tasks(user_id="bob")
        self.assertEqual(len(bob_tasks), 1)
        self.assertEqual(bob_tasks[0].user_id, "bob")

        t1.cancel()
        t2.cancel()
        await asyncio.sleep(0.05)

    async def test_list_tasks_filters_by_session(self) -> None:
        """list_tasks with session_id filter narrows results."""

        async def _work() -> None:
            await asyncio.sleep(100)

        t1 = asyncio.create_task(_work())
        t2 = asyncio.create_task(_work())
        await self.mgr.register_task(
            asyncio_task=t1,
            session_id="s1",
            agent_id="a1",
            user_id="alice",
            tool_name="tool_a",
        )
        await self.mgr.register_task(
            asyncio_task=t2,
            session_id="s2",
            agent_id="a2",
            user_id="alice",
            tool_name="tool_b",
        )

        filtered = self.mgr.list_tasks(
            user_id="alice",
            session_id="s1",
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].session_id, "s1")

        t1.cancel()
        t2.cancel()
        await asyncio.sleep(0.05)

    async def test_get_task_returns_none_for_other_user(self) -> None:
        """get_task returns None when user_id doesn't match."""

        async def _work() -> None:
            await asyncio.sleep(100)

        t = asyncio.create_task(_work())
        task_id = await self.mgr.register_task(
            asyncio_task=t,
            session_id="s1",
            agent_id="a1",
            user_id="alice",
            tool_name="tool_a",
        )

        self.assertIsNotNone(
            self.mgr.get_task(task_id, user_id="alice"),
        )
        self.assertIsNone(
            self.mgr.get_task(task_id, user_id="bob"),
        )

        t.cancel()
        await asyncio.sleep(0.05)

    async def test_get_task_returns_none_for_missing_id(self) -> None:
        """get_task returns None for a non-existent task_id."""
        self.assertIsNone(
            self.mgr.get_task("nonexistent", user_id="anyone"),
        )


class TestMakeSummary(IsolatedAsyncioTestCase):
    """Test the _make_summary helper function."""

    async def test_empty_input_returns_tool_name(self) -> None:
        """Empty or None input returns just the tool name."""
        self.assertEqual(_make_summary("Bash", None), "Bash")
        self.assertEqual(_make_summary("Bash", ""), "Bash")

    async def test_dict_input_includes_key_values(self) -> None:
        """JSON dict input produces key=value summary."""
        raw = json.dumps({"command": "ls -la", "timeout": 30})
        result = _make_summary("Bash", raw)
        self.assertIn("Bash(", result)
        self.assertIn("command=ls -la", result)
        self.assertIn("timeout=30", result)
        self.assertTrue(result.endswith(")"))

    async def test_respects_max_length(self) -> None:
        """Summary never exceeds _SUMMARY_MAX_LEN characters."""
        raw = json.dumps({"key": "x" * 200, "other": "y" * 200})
        result = _make_summary("VeryLongToolName", raw)
        self.assertLessEqual(len(result), 128)

    async def test_non_json_input_is_truncated(self) -> None:
        """Non-JSON input is included as-is with truncation."""
        raw = "a" * 100
        result = _make_summary("Tool", raw)
        self.assertIn("Tool(", result)
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 128)

    async def test_non_json_short_input_no_ellipsis(self) -> None:
        """Short non-JSON input doesn't get a misleading ellipsis."""
        result = _make_summary("Tool", "hi")
        self.assertEqual(result, "Tool(hi)")

    async def test_empty_dict_returns_tool_name(self) -> None:
        """Empty JSON dict returns just the tool name."""
        result = _make_summary("Tool", "{}")
        self.assertEqual(result, "Tool")

    async def test_non_dict_json_returns_tool_name(self) -> None:
        """JSON that's not a dict returns just the tool name."""
        result = _make_summary("Tool", "[1, 2, 3]")
        self.assertEqual(result, "Tool")

    async def test_long_values_are_truncated(self) -> None:
        """Individual values longer than 40 chars are truncated."""
        raw = json.dumps({"file": "/" + "a" * 100})
        result = _make_summary("Read", raw)
        self.assertIn("...", result)
        self.assertLessEqual(len(result), 128)

    async def test_multiple_keys_included_within_budget(self) -> None:
        """Multiple short key-values are all included."""
        raw = json.dumps({"a": "1", "b": "2", "c": "3"})
        result = _make_summary("T", raw)
        self.assertIn("a=1", result)
        self.assertIn("b=2", result)
        self.assertIn("c=3", result)
