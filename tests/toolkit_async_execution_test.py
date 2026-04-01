# -*- coding: utf-8 -*-
# pylint: disable=protected-access, redefined-builtin
"""Test async execution functionality in Toolkit."""
import asyncio
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase

from agentscope.message import ToolUseBlock, TextBlock
from agentscope.tool import ToolResponse, Toolkit


def _text(res: ToolResponse) -> str:
    """Extract concatenated text from a ToolResponse."""
    return "".join(
        block["text"] for block in res.content if block.get("type") == "text"
    )


async def slow_async_func(delay: float) -> ToolResponse:
    """A slow async function for testing async execution.

    Args:
        delay (`float`):
            The time to sleep in seconds.
    """
    await asyncio.sleep(delay)
    return ToolResponse(
        content=[TextBlock(type="text", text=f"done after {delay}s")],
    )


async def slow_async_generator_func(
    delay: float,
) -> AsyncGenerator[ToolResponse, None]:
    """A slow async generator function for testing async execution.

    Args:
        delay (`float`):
            The time to sleep in seconds.
    """
    yield ToolResponse(
        content=[TextBlock(type="text", text="chunk1")],
        stream=True,
    )
    await asyncio.sleep(delay)
    yield ToolResponse(
        content=[TextBlock(type="text", text="chunk1chunk2")],
        stream=True,
        is_last=True,
    )


class ToolkitAsyncExecutionTest(IsolatedAsyncioTestCase):
    """Tests for async execution via call_tool_function."""

    async def asyncSetUp(self) -> None:
        self.toolkit = Toolkit()

    async def asyncTearDown(self) -> None:
        self.toolkit = None

    def _make_tool_call(self, name: str, input: dict) -> ToolUseBlock:
        return ToolUseBlock(
            type="tool_use",
            id="test-id",
            name=name,
            input=input,
        )

    async def _start_async_task(self, delay: float) -> str | None:
        """Register slow_async_func, call it with async_execution=True,
        and return the task_id from the response text."""
        self.toolkit.register_tool_function(
            slow_async_func,
            async_execution=True,
        )
        res = await self.toolkit.call_tool_function(
            self._make_tool_call("slow_async_func", {"delay": delay}),
        )
        task_id: str | None = None
        async for chunk in res:
            # The response text contains "Task ID: <id>"
            for block in chunk.content:
                if "Task ID:" in block["text"]:
                    task_id = (
                        block["text"]
                        .split("Task ID:")[1]
                        .strip()
                        .split(".")[0]
                        .strip()
                    )
        self.assertIsNotNone(
            task_id,
            "task_id should be present in response",
        )
        return task_id

    # ------------------------------------------------------------------
    # 1. view_task: still running vs completed
    # ------------------------------------------------------------------

    async def test_view_task_still_running(self) -> None:
        """view_task returns 'still running' when task is not yet done."""
        task_id = await self._start_async_task(delay=5.0)

        # Task should still be running immediately after launch
        res = await self.toolkit.view_task(task_id)
        self.assertIn(task_id, _text(res))
        self.assertIn("still running", _text(res))

        # Clean up: cancel the task so it doesn't linger
        await self.toolkit.cancel_task(task_id)

    async def test_view_task_completed(self) -> None:
        """view_task returns the result once the task has finished."""
        task_id = await self._start_async_task(delay=0.05)

        # Wait for the task to finish
        await asyncio.sleep(0.2)

        res = await self.toolkit.view_task(task_id)
        self.assertIn("done after 0.05s", _text(res))

        # Result should have been consumed; task_id no longer tracked
        self.assertNotIn(task_id, self.toolkit._async_results)
        self.assertNotIn(task_id, self.toolkit._async_tasks)

    # ------------------------------------------------------------------
    # 2. wait_task: completes within timeout vs times out
    # ------------------------------------------------------------------

    async def test_wait_task_completes_within_timeout(self) -> None:
        """wait_task returns the result when task finishes before timeout."""
        task_id = await self._start_async_task(delay=0.05)

        res = await self.toolkit.wait_task(task_id, timeout=5.0)
        self.assertIn("done after 0.05s", _text(res))

    async def test_wait_task_timeout(self) -> None:
        """wait_task returns a timeout message when task exceeds timeout."""
        task_id = await self._start_async_task(delay=5.0)

        res = await self.toolkit.wait_task(task_id, timeout=0.05)
        self.assertIn("still running", _text(res))

        # Task should still be alive after timeout
        self.assertIn(task_id, self.toolkit._async_tasks)

        # Clean up
        await self.toolkit.cancel_task(task_id)

    # ------------------------------------------------------------------
    # 3. cancel_task
    # ------------------------------------------------------------------

    async def test_cancel_task(self) -> None:
        """cancel_task cancels a running task successfully."""
        task_id = await self._start_async_task(delay=5.0)

        res = await self.toolkit.cancel_task(task_id)
        self.assertIn("cancelled", _text(res).lower())

        # Task should be removed from active tasks
        self.assertNotIn(task_id, self.toolkit._async_tasks)

    async def test_cancel_already_completed_task(self) -> None:
        """cancel_task on a completed task returns an appropriate message."""
        task_id = await self._start_async_task(delay=0.05)

        # Wait for completion
        await asyncio.sleep(0.2)

        res = await self.toolkit.cancel_task(task_id)
        self.assertIn("already completed", _text(res).lower())

    # ------------------------------------------------------------------
    # 4. Streaming tool function: chunks are accumulated into one result
    # ------------------------------------------------------------------

    async def test_async_generator_result_is_accumulated(self) -> None:
        """Streaming tool results are accumulated into a single
        ToolResponse."""
        self.toolkit.register_tool_function(
            slow_async_generator_func,
            async_execution=True,
        )
        res = await self.toolkit.call_tool_function(
            self._make_tool_call(
                "slow_async_generator_func",
                {"delay": 0.05},
            ),
        )
        task_id = None
        async for chunk in res:
            for block in chunk.content:
                if "Task ID:" in block["text"]:
                    task_id = (
                        block["text"]
                        .split("Task ID:")[1]
                        .strip()
                        .split(".")[0]
                        .strip()
                    )
        self.assertIsNotNone(task_id)

        # Wait for the generator to finish
        result = await self.toolkit.wait_task(task_id, timeout=5.0)

        # Both chunks' content should be present in the accumulated result
        text = _text(result)
        self.assertIn("chunk1", text)
        self.assertIn("chunk2", text)

    # ------------------------------------------------------------------
    # 5. Invalid task_id
    # ------------------------------------------------------------------

    async def test_view_invalid_task_id(self) -> None:
        """view_task with unknown task_id returns an error message."""
        res = await self.toolkit.view_task("nonexistent-id")
        self.assertIn("InvalidTaskIdError", _text(res))

    async def test_cancel_invalid_task_id(self) -> None:
        """cancel_task with unknown task_id returns an error message."""
        res = await self.toolkit.cancel_task("nonexistent-id")
        self.assertIn("InvalidTaskIdError", _text(res))

    async def test_wait_invalid_task_id(self) -> None:
        """wait_task with unknown task_id returns an error message."""
        res = await self.toolkit.wait_task("nonexistent-id", timeout=1.0)
        self.assertIn("InvalidTaskIdError", _text(res))
