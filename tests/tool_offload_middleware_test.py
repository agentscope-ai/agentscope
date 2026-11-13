# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for ToolOffloadMiddleware."""
import asyncio
import gc
import json
import weakref
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

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


class PolicyTool(ToolBase):
    """A slow tool whose offload policy is configurable per instance."""

    name: str = "policy_tool"
    description: str = "A slow tool with a configurable offload policy."
    input_schema: dict = _SlowToolParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        is_offloadable: bool = True,
        offload_timeout_secs: float | None = None,
    ) -> None:
        """Set the per-instance offload policy.

        Args:
            is_offloadable (`bool`):
                Whether the tool may be offloaded at all.
            offload_timeout_secs (`float | None`):
                Per-tool offload timeout override; ``None`` uses the
                middleware default.
        """
        super().__init__()
        self.is_offloadable = is_offloadable
        self.offload_timeout_secs = offload_timeout_secs

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
            content=[TextBlock(text=f"PolicyTool finished after {delay}s")],
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

    async def test_offloaded_stream_releases_intermediate_chunks(self) -> None:
        """Detaching the foreground releases old and future stream events."""
        for outcome in ("success", "error", "cancel"):
            with self.subTest(outcome=outcome):
                await self._check_stream_retention(outcome)

    async def _check_stream_retention(self, outcome: str) -> None:
        """Run one isolated producer/consumer handoff."""
        agent, middleware = self._make_agent(
            Toolkit(tools=[SlowTool()]),
            timeout_secs=0.01,
        )
        start = asyncio.Event()
        streamed = asyncio.Event()
        finish = asyncio.Event()
        delivered = asyncio.Event()
        refs: list[weakref.ReferenceType] = []

        async def handler(**_kwargs: Any) -> AsyncGenerator:
            """Emit a foreground chunk and a background burst."""
            item = ToolChunk(content=[TextBlock(text="foreground")])
            refs.append(weakref.ref(item))
            yield item
            del item
            await start.wait()
            for index in range(32):
                item = ToolChunk(
                    content=[
                        TextBlock(
                            text=f"{index}:" + "x" * 65536,
                        ),
                    ],
                )
                refs.append(weakref.ref(item))
                yield item
                del item
            streamed.set()
            await finish.wait()
            if outcome == "error":
                raise ValueError("stream failed")
            yield ToolResponse(
                id="stream-retention",
                content=[TextBlock(text="done")],
            )

        async def push(key: str, *_args: Any, **_kwargs: Any) -> None:
            """Observe real notification delivery without sleeps."""
            if key == MessageBusKeys.inbox(agent.state.session_id):
                delivered.set()

        middleware._message_bus.queue_push.side_effect = push
        stream = middleware.on_acting(
            agent,
            {
                "tool_call": ToolCallBlock(
                    id="stream-retention",
                    name="slow_tool",
                    input="{}",
                ),
            },
            handler,
        )
        await asyncio.wait_for(anext(stream), 2)
        task = next(iter(self.bg_manager.tasks.values())).asyncio_task
        try:
            # Keep on_acting suspended at its synthetic response too.
            start.set()
            await asyncio.wait_for(streamed.wait(), 2)
            gc.collect()
            self.assertEqual(sum(ref() is not None for ref in refs), 0)
            if outcome == "cancel":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertFalse(delivered.is_set())
            else:
                finish.set()
                await asyncio.wait_for(delivered.wait(), 2)
                push_calls = middleware._message_bus.queue_push.call_args_list
                calls = [
                    call
                    for call in push_calls
                    if call.args[0]
                    == MessageBusKeys.inbox(agent.state.session_id)
                ]
                self.assertEqual(len(calls), 1)
                self.assertIn(
                    "stream failed" if outcome == "error" else "done",
                    str(calls[0]),
                )
        finally:
            start.set()
            finish.set()
            await asyncio.gather(task, return_exceptions=True)
            await stream.aclose()

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

        offloaded_at = 1794240000.0
        with patch(
            "agentscope.app.middleware._tool_offload_middleware.time",
        ) as mock_time:
            mock_time.time.return_value = offloaded_at
            results = [item async for item in agent._acting(tool_call)]

        self.assertEqual(len(self.bg_manager.tasks), 1)
        task_id = next(iter(self.bg_manager.tasks))
        expected_response = {
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
            "metadata": {
                "background_task_id": task_id,
                "tool_name": "slow_tool",
                "tool_state": "running",
                "offloaded_at": offloaded_at,
            },
            "id": "call_slow",
        }
        self.assertEqual(
            [item.model_dump() for item in results],
            [
                {
                    **expected_response,
                    "id": AnyString(),
                    "is_last": True,
                },
                expected_response,
            ],
        )
        text = results[1].content[0].text  # type: ignore[union-attr]
        self.assertIn("background", text)
        self.assertIn("id=", text)

    async def test_execution_start_is_captured_before_offload(self) -> None:
        """Capture execution start before the handler changes the clock."""
        agent, middleware = self._make_agent(
            Toolkit(tools=[SlowTool()]),
            timeout_secs=0.01,
        )
        gate = asyncio.Event()
        tool_call = ToolCallBlock(
            id="call_start",
            name="slow_tool",
            input='{"delay": 30}',
        )
        with patch(f"{ToolOffloadMiddleware.__module__}.time") as clock:
            clock.time.return_value = 100.0

            async def handler(**_kwargs: Any) -> AsyncGenerator:
                """Advance wall time after execution has already begun."""
                clock.time.return_value = 110.0
                await gate.wait()
                yield ToolResponse(content=[TextBlock(text="done")])

            async for _ in middleware.on_acting(
                agent,
                {"tool_call": tool_call},
                handler,
            ):
                pass
            (bg_task,) = self.bg_manager.tasks.values()
            self.assertEqual(
                {
                    "execution_started_at": bg_task.execution_started_at,
                    "tool_name": bg_task.tool_name,
                    "clock_at_offload": clock.time(),
                },
                {
                    "execution_started_at": 100.0,
                    "tool_name": "slow_tool",
                    "clock_at_offload": 110.0,
                },
            )
            gate.set()
            await asyncio.wait_for(bg_task.asyncio_task, timeout=1)

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

    async def test_interrupted_response_matches_record_and_inbox(self) -> None:
        """Interrupted results keep their output and report cancellation."""
        for output in ("", "partial output"):
            with self.subTest(output=output):
                agent, middleware = self._make_agent(
                    Toolkit(tools=[SlowTool()]),
                    timeout_secs=0.01,
                )
                gate = asyncio.Event()
                delivered = asyncio.Event()
                mock_bus = middleware._message_bus
                mock_bus.publish.side_effect = (
                    lambda *_args, event=delivered, **_kwargs: event.set()
                )
                response = ToolResponse(
                    id="call_interrupted",
                    state=ToolResultState.INTERRUPTED,
                    content=[TextBlock(text=output)] if output else [],
                )
                original = response.model_dump(mode="json")

                async def handler(
                    *,
                    event: asyncio.Event = gate,
                    result: ToolResponse = response,
                    **_kwargs: Any,
                ) -> AsyncGenerator:
                    await event.wait()
                    yield result

                call = ToolCallBlock(
                    id="call_interrupted",
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
                await asyncio.wait_for(delivered.wait(), timeout=1)
                record = await self.bg_manager.get_task(bg.id, user_id="u")
                expected.update(
                    status=TaskStatus.CANCELLED,
                    completed_at=record.completed_at,
                )
                self.assertEqual(record.model_dump(), expected)

                prefix = (
                    "<system-notification>Tool 'slow_tool' running in "
                    "background (id=call_interrupted) has been cancelled"
                )
                expected_hint: str | list[dict]
                if output:
                    expected_hint = [
                        dict(
                            original["content"][0],
                            text=f"{prefix}.\n\nResult:\n\n{output}"
                            "</system-notification>",
                        ),
                    ]
                else:
                    expected_hint = (
                        f"{prefix} with no output.</system-notification>"
                    )
                inbox = [
                    call.args[1]
                    for call in mock_bus.queue_push.call_args_list
                    if call.args[0]
                    == MessageBusKeys.inbox(
                        agent.state.session_id,
                    )
                ]
                self.assertEqual(
                    inbox,
                    [
                        {
                            "type": "hint",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": AnyString(),
                            "source": json.dumps(
                                {
                                    "label": "tool_output",
                                    "sublabel": "slow_tool · call_interrupted",
                                },
                                ensure_ascii=False,
                            ),
                            "hint": expected_hint,
                        },
                    ],
                )
                self.assertEqual(response.model_dump(mode="json"), original)

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

    async def test_tool_opting_out_of_offload_runs_synchronously(
        self,
    ) -> None:
        """A tool with ``is_offloadable=False`` never gets offloaded,
        even when it outlives the middleware timeout."""

        toolkit = Toolkit(tools=[PolicyTool(is_offloadable=False)])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_no_offload",
            name="policy_tool",
            input=json.dumps({"delay": 0.5}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        # The real result must come back in this call, not a synthetic
        # placeholder, and nothing may be registered in the background.
        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("PolicyTool finished after 0.5s", text)
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_per_tool_timeout_override_extends_the_wait(
        self,
    ) -> None:
        """A tool that overrides ``offload_timeout_secs`` with a longer
        value is not offloaded at the middleware-wide deadline."""

        toolkit = Toolkit(tools=[PolicyTool(offload_timeout_secs=1.0)])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_longer_wait",
            name="policy_tool",
            input=json.dumps({"delay": 0.3}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("PolicyTool finished after 0.3s", text)
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_per_tool_timeout_override_shortens_the_wait(
        self,
    ) -> None:
        """A tool that overrides ``offload_timeout_secs`` with a shorter
        value is offloaded before the middleware-wide deadline fires."""

        toolkit = Toolkit(tools=[PolicyTool(offload_timeout_secs=0.01)])
        agent, _ = self._make_agent(toolkit, timeout_secs=5.0)

        tool_call = ToolCallBlock(
            id="call_shorter_wait",
            name="policy_tool",
            input=json.dumps({"delay": 0.5}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("running in background", text)
        # The placeholder must report the effective (per-tool) timeout,
        # not the middleware-wide one.
        self.assertIn("for over 0.01s", text)
        self.assertEqual(len(self.bg_manager.tasks), 1)
