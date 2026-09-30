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
from agentscope.app.message_bus import MessageBus, MessageBusKeys
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.app._manager import BackgroundTaskManager
from agentscope.event import ReplyEndEvent
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
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


class _BlockingToolParams(BaseModel):
    """Parameters for the blocking test tool."""

    tag: str


class BlockingTool(ToolBase):
    """A tool that blocks until it is cancelled and records its calls."""

    name: str = "blocking_tool"
    description: str = "A blocking tool for testing interruption."
    input_schema: dict = _BlockingToolParams.model_json_schema()
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, n_calls: int = 1) -> None:
        """Initialize the tool.

        Args:
            n_calls (`int`, defaults to ``1``):
                The number of calls to wait for before ``started`` is set.
        """
        self.records: list[str] = []
        self.started = asyncio.Event()
        self._n_calls = n_calls

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
        tag: str,
    ) -> ToolChunk:
        """Record the start, then block until cancelled.

        Args:
            tag (`str`):
                The label recorded for this call.

        Returns:
            `ToolChunk`:
                A chunk containing the tag, never reached in the tests.
        """
        self.records.append(f"start {tag}")
        if len(self.records) == self._n_calls:
            self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.records.append(f"cancelled {tag}")
            raise
        return ToolChunk(content=[TextBlock(text=f"BlockingTool: {tag}")])


class ConcurrentBlockingTool(BlockingTool):
    """A concurrency-safe blocking tool, run in parallel by the agent."""

    name: str = "concurrent_blocking_tool"
    is_concurrency_safe: bool = True


def _interrupted_result(tool_call_id: str, name: str) -> dict:
    """The tool result the toolkit yields for a cancelled tool call."""
    return {
        "type": "tool_result",
        "id": tool_call_id,
        "name": name,
        "output": [
            {
                "type": "text",
                "text": "<system-reminder>The tool call has been "
                "interrupted by the user.</system-reminder>",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
            },
        ],
        "state": "interrupted",
        "metadata": {},
        "created_at": AnyString(),
        "finished_at": None,
    }


class ToolOffloadMiddlewareTest(IsolatedAsyncioTestCase):
    """Test cases for the ToolOffloadMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = MockModel()
        self.bg_manager = BackgroundTaskManager(
            message_bus=MagicMock(spec=MessageBus),
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

    async def _reply_and_interrupt(
        self,
        agent: Agent,
        tool: BlockingTool,
        tool_calls: list[ToolCallBlock],
    ) -> list:
        """Run a reply that makes the given tool calls, and cancel it once
        they have all started, as the service's interrupt does.

        Args:
            agent (`Agent`):
                The agent to run.
            tool (`BlockingTool`):
                The tool the calls go to.
            tool_calls (`list[ToolCallBlock]`):
                The tool calls the model makes.

        Returns:
            `list`:
                The events of the reply.
        """
        self.mock_model.set_responses(
            [[ChatResponse(content=tool_calls, is_last=True)]],
        )
        events: list = []

        async def _drive() -> None:
            async for evt in agent.reply_stream(
                UserMsg(name="user", content="Hi"),
            ):
                events.append(evt)

        task = asyncio.create_task(_drive())
        await tool.started.wait()
        task.cancel()
        # A tool that isn't cancelled keeps the reply going, so don't wait
        # for it forever
        await asyncio.wait([task], timeout=1)
        return events

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
        # Removed from manager
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_interrupt_cancels_running_tool(self) -> None:
        """Interrupting the reply cancels the running tool, and the agent
        gets the interrupted result the toolkit yields for it."""

        tool = BlockingTool()
        agent, _ = self._make_agent(Toolkit(tools=[tool]), timeout_secs=5.0)

        events = await self._reply_and_interrupt(
            agent,
            tool,
            [
                ToolCallBlock(
                    id="call_0",
                    name=tool.name,
                    input=json.dumps({"tag": "r0"}),
                ),
            ],
        )

        self.assertListEqual(tool.records, ["start r0", "cancelled r0"])
        self.assertListEqual(
            [
                e.finished_reason
                for e in events
                if isinstance(e, ReplyEndEvent)
            ],
            ["interrupted"],
        )
        self.assertListEqual(
            [
                block.model_dump(mode="json")
                for block in agent.state.context[-1].content
                if block.type == "tool_result"
            ],
            [_interrupted_result("call_0", tool.name)],
        )
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_interrupt_parallel_tools_runs_each_once(self) -> None:
        """Interrupting a parallel round cancels every tool and ends the
        reply as interrupted, without running the calls again."""

        tool = ConcurrentBlockingTool(n_calls=2)
        agent, _ = self._make_agent(Toolkit(tools=[tool]), timeout_secs=5.0)

        events = await self._reply_and_interrupt(
            agent,
            tool,
            [
                ToolCallBlock(
                    id=f"call_{i}",
                    name=tool.name,
                    input=json.dumps({"tag": f"r{i}"}),
                )
                for i in range(2)
            ],
        )

        self.assertListEqual(
            tool.records,
            ["start r0", "start r1", "cancelled r0", "cancelled r1"],
        )
        self.assertListEqual(
            [
                e.finished_reason
                for e in events
                if isinstance(e, ReplyEndEvent)
            ],
            ["interrupted"],
        )
        self.assertListEqual(
            [
                block.model_dump(mode="json")
                for block in agent.state.context[-1].content
                if block.type == "tool_result"
            ],
            [
                _interrupted_result("call_0", tool.name),
                _interrupted_result("call_1", tool.name),
            ],
        )
        self.assertEqual(self.mock_model.cnt, 1)
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_interrupt_without_interrupted_result_reraises(
        self,
    ) -> None:
        """If the cancelled task yields no interrupted result, because it
        was cancelled before it reached the toolkit, the cancellation is
        re-raised instead of swallowed."""

        agent, middleware = self._make_agent(
            Toolkit(tools=[FastTool()]),
            timeout_secs=5.0,
        )
        entered = asyncio.Event()
        records: list[str] = []

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                records.append("cancelled")
                raise
            yield ToolChunk(content=[TextBlock(text="never reached")])

        async def _consume() -> None:
            async for _ in middleware.on_acting(
                agent=agent,
                input_kwargs={
                    "tool_call": ToolCallBlock(
                        id="call_early",
                        name="fast_tool",
                        input=json.dumps({"value": "hello"}),
                    ),
                },
                next_handler=next_handler,
            ):
                pass

        task = asyncio.create_task(_consume())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertListEqual(records, ["cancelled"])
