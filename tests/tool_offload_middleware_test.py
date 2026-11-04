# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for ToolOffloadMiddleware."""
import asyncio
import json
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel


from utils import AnyString, MockModel

from agentscope.agent import Agent
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    MessageBusKeys,
)
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.app._manager import BackgroundTaskManager, TaskStatus
from agentscope.app._manager._background_task_manager import (
    BackgroundTaskRecord,
)
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit, ToolResponse


class _SlowToolParams(BaseModel):
    """Parameters for the slow test tool."""

    delay: float


class SlowTool(ToolBase):
    """A tool that sleeps for ``delay`` seconds before returning."""

    name: str = "slow_tool"
    description: str = "A slow tool for testing background offload."
    input_schema: dict = _SlowToolParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(  # type: ignore[override]
        self,
        delay: float,
    ) -> ToolChunk:
        """Sleep for *delay* seconds then return a result.

        Args:
            delay (`float`):
                Seconds to sleep.

        Returns:
            `ToolChunk`:
                A chunk containing the result text.
        """
        await asyncio.sleep(delay)
        return ToolChunk(
            content=[TextBlock(text=f"SlowTool finished after {delay}s")],
        )


class _FastToolParams(BaseModel):
    """Parameters for the fast test tool."""

    value: str


class FastTool(ToolBase):
    """A tool that returns immediately."""

    name: str = "fast_tool"
    description: str = "A fast tool for testing normal execution."
    input_schema: dict = _FastToolParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(  # type: ignore[override]
        self,
        value: str,
    ) -> ToolChunk:
        """Return a chunk with *value*.

        Args:
            value (`str`):
                The value to echo.

        Returns:
            `ToolChunk`:
                A chunk containing the value.
        """
        return ToolChunk(
            content=[TextBlock(text=f"FastTool: {value}")],
        )


class ToolOffloadMiddlewareTest(IsolatedAsyncioTestCase):
    """Test cases for the ToolOffloadMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = MockModel()
        self.bg_manager = await self.enterAsyncContext(
            BackgroundTaskManager(message_bus=InMemoryMessageBus()),
        )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_agent(
        self,
        toolkit: Toolkit,
        timeout_secs: float,
    ) -> tuple[Agent, ToolOffloadMiddleware]:
        """Create an agent with ToolOffloadMiddleware attached.

        Args:
            toolkit (`Toolkit`):
                The toolkit to attach to the agent.
            timeout_secs (`float`):
                The middleware timeout.

        Returns:
            `tuple[Agent, ToolOffloadMiddleware]`:
                The configured agent and the middleware instance.
        """
        # No run is registered as this session's inbox consumer, so a
        # completed background tool is expected to wake the session.
        # ``spec`` alone would hand back a truthy sentinel and make the
        # delivery look like somebody was already going to drain it.
        message_bus = MagicMock(spec=MessageBus)
        message_bus.registry_get = AsyncMock(return_value=None)
        middleware = ToolOffloadMiddleware(
            bg_manager=self.bg_manager,
            message_bus=message_bus,
            user_id="u",
            agent_id="a",
            timeout_secs=timeout_secs,
        )
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=toolkit,
            middlewares=[middleware],
        )
        return agent, middleware

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    async def test_fast_tool_completes_normally(self) -> None:
        """A tool that finishes within the timeout yields its real result."""

        toolkit = Toolkit(tools=[FastTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=5.0)

        tool_call = ToolCallBlock(
            id="call_fast",
            name="fast_tool",
            input=json.dumps({"value": "hello"}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        # Should yield real ToolResponse (not synthetic)
        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("FastTool: hello", text)
        # No background tasks registered
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_slow_tool_offloaded_to_background(self) -> None:
        """A tool that exceeds timeout returns a synthetic result."""

        toolkit = Toolkit(tools=[SlowTool()])
        # Set a very short timeout so the 0.5s tool is always offloaded
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_slow",
            name="slow_tool",
            input=json.dumps({"delay": 0.5}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        # Should yield a synthetic ToolResponse immediately
        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        self.assertDictEqual(
            responses[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": AnyString(),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "call_slow",
            },
        )
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("background", text)
        self.assertIn("id=", text)

        # Background task should be registered
        self.assertEqual(len(self.bg_manager.tasks), 1)

    async def test_background_task_result_injected_into_context(
        self,
    ) -> None:
        """After the background tool finishes, the result is pushed to the
        session inbox on the message bus as a serialised HintBlock."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, middleware = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_bg",
            name="slow_tool",
            input=json.dumps({"delay": 0.2}),
        )

        # Trigger offload
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        # Wait long enough for the background tool (0.2s) to finish
        await asyncio.sleep(0.4)

        # The completed result should now have been pushed to the message bus
        # inbox as a model-dumped HintBlock.
        mock_bus = middleware._message_bus
        # The middleware uses queue_push(inbox_key, payload) rather
        # than the deprecated inbox_push(session_id, payload).
        inbox_calls = [
            c
            for c in mock_bus.queue_push.call_args_list
            if c.args[0] == MessageBusKeys.inbox(agent.state.session_id)
        ]
        self.assertEqual(len(inbox_calls), 1)
        _, hint_dict = inbox_calls[0].args
        session_id_called = agent.state.session_id
        self.assertEqual(session_id_called, agent.state.session_id)
        self.maxDiff = None
        self.assertDictEqual(
            hint_dict,
            {
                "type": "hint",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "source": '{"label": "tool_output", "sublabel": "slow_tool · '
                'call_bg"}',
                "hint": [
                    {
                        "type": "text",
                        "text": AnyString(),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
            },
        )
        hint_text = hint_dict["hint"][0]["text"]
        self.assertIn("SlowTool finished", hint_text)
        self.assertIn("<system-notification>", hint_text)

    async def test_background_task_triggers_wakeup_on_completion(
        self,
    ) -> None:
        """After a background tool finishes, a wakeup is enqueued on the
        message bus so that an idle session can be restarted automatically."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, middleware = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_wakeup",
            name="slow_tool",
            input=json.dumps({"delay": 0.2}),
        )

        # Trigger offload
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        # Wait long enough for the background tool (0.2s) to finish
        await asyncio.sleep(0.4)

        # enqueue_wakeup must be called exactly once with the correct ids so
        # WakeupDispatcher can re-invoke ChatService.run for this session.
        mock_bus = middleware._message_bus
        # enqueue_run_trigger is a standalone function that calls
        # bus.queue_push + bus.publish under the hood.
        wakeup_calls = [
            c
            for c in mock_bus.queue_push.call_args_list
            if c.args[0] == MessageBusKeys.wakeup_queue()
        ]
        self.assertEqual(len(wakeup_calls), 1)
        payload = wakeup_calls[0].args[1]
        self.assertEqual(payload["user_id"], "u")
        self.assertEqual(payload["session_id"], agent.state.session_id)
        self.assertEqual(payload["agent_id"], "a")

    async def test_background_failures_retain_error_status(self) -> None:
        """Error responses and raised exceptions both retain failed state."""
        for raises in (False, True):
            with self.subTest(raises=raises):
                agent, middleware = self._make_agent(
                    Toolkit(tools=[SlowTool()]),
                    timeout_secs=0.01,
                )
                gate = asyncio.Event()

                async def handler(
                    *,
                    event: asyncio.Event = gate,
                    should_raise: bool = raises,
                    **_kwargs: Any,
                ) -> AsyncGenerator:
                    """Fail only after the tool has been offloaded."""
                    await event.wait()
                    if should_raise:
                        raise ValueError("tool execution failed")
                    yield ToolResponse(
                        id="call_failure",
                        state=ToolResultState.ERROR,
                        content=[TextBlock(text="tool execution failed")],
                    )

                call = ToolCallBlock(
                    id="call_failure",
                    name="slow_tool",
                    input="{}",
                )
                async for _ in middleware.on_acting(
                    agent,
                    {"tool_call": call},
                    handler,
                ):
                    pass
                bg = next(iter(self.bg_manager.tasks.values()))
                expected = BackgroundTaskRecord.model_validate(bg).model_dump()
                gate.set()
                await bg.asyncio_task
                await asyncio.gather(*self.bg_manager._completion_tasks)
                record = await self.bg_manager.get_task(bg.id, user_id="u")
                self.assertIsNotNone(record.completed_at)
                expected.update(
                    status=TaskStatus.FAILED,
                    error_summary="tool execution failed",
                    completed_at=record.completed_at,
                )
                self.assertEqual(record.model_dump(), expected)
                self.assertEqual(self.bg_manager.tasks, {})

    async def test_tool_stop_cancels_background_task(self) -> None:
        """ToolStop tool cancels the running background asyncio task."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_cancel",
            name="slow_tool",
            input=json.dumps({"delay": 10.0}),
        )

        # Offload the slow tool
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        self.assertEqual(len(self.bg_manager.tasks), 1)
        task_id = next(iter(self.bg_manager.tasks))
        bg_task = self.bg_manager.tasks[task_id]
        asyncio_task = bg_task.asyncio_task

        # Call ToolStop bound to the same session as the registered
        # background task, so the local cancel path matches.
        tool_stop_tools = await self.bg_manager.list_tools(
            session_id=bg_task.session_id,
        )
        tool_stop = tool_stop_tools[0]
        result = await tool_stop(task_id=task_id)
        text = result.content[0].text  # type: ignore[union-attr]
        self.assertIn("stopped successfully", text)

        # The asyncio task should be cancelling
        self.assertTrue(asyncio_task.cancelled() or asyncio_task.cancelling())
        await asyncio.gather(asyncio_task, return_exceptions=True)
        await asyncio.gather(*self.bg_manager._completion_tasks)
        # The handle is released, but cancelled metadata remains queryable.
        self.assertEqual(len(self.bg_manager.tasks), 0)
        record = await self.bg_manager.get_task(task_id, user_id="u")
        self.assertEqual(record.status, TaskStatus.CANCELLED)
