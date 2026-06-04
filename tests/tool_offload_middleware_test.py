# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for ToolOffloadMiddleware."""
import asyncio
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from pydantic import BaseModel


from utils import AnyString, MockModel

from agentscope.agent import Agent
from agentscope.app.message_bus import MessageBus
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.app._manager import BackgroundTaskManager
from agentscope.message import TextBlock, ToolCallBlock
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
        self.bg_manager = BackgroundTaskManager()

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
        middleware = ToolOffloadMiddleware(
            bg_manager=self.bg_manager,
            message_bus=MagicMock(spec=MessageBus),
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
        mock_bus.inbox_push.assert_called_once()
        session_id_called, hint_dict = mock_bus.inbox_push.call_args.args
        self.assertEqual(session_id_called, agent.state.session_id)
        self.maxDiff = None
        self.assertDictEqual(
            hint_dict,
            {
                "type": "hint",
                "id": AnyString(),
                "source": '{"label": "tool_output", "sublabel": "slow_tool · '
                'call_bg"}',
                "hint": [
                    {
                        "type": "text",
                        "text": AnyString(),
                        "id": AnyString(),
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
        mock_bus.enqueue_wakeup.assert_called_once_with(
            user_id="u",
            session_id=agent.state.session_id,
            agent_id="a",
        )

    async def test_task_stop_cancels_background_task(self) -> None:
        """TaskStop tool cancels the running background asyncio task."""

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
        asyncio_task = self.bg_manager.tasks[task_id].asyncio_task

        # Call TaskStop
        task_stop_tools = await self.bg_manager.list_tools()
        task_stop = task_stop_tools[0]
        result = await task_stop(task_id=task_id)
        text = result.content[0].text  # type: ignore[union-attr]
        self.assertIn("stopped successfully", text)

        # The asyncio task should be cancelling
        self.assertTrue(asyncio_task.cancelled() or asyncio_task.cancelling())
        # Removed from manager
        self.assertEqual(len(self.bg_manager.tasks), 0)
