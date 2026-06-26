# -*- coding: utf-8 -*-
# pylint: disable=abstract-method
"""Tests for on_acting middleware patterns: short-circuit, chaining, and
background-task execution.

These tests complement middleware_test.py (single-intercept) and
tool_offload_middleware_test.py (ToolOffloadMiddleware integration) by
covering the structural properties of the on_acting hook:

1. A middleware can short-circuit execution and return a synthetic result
   without ever calling next_handler.
2. Multiple on_acting middlewares form a correct onion (LIFO) chain.
3. Draining next_handler via a background asyncio.Task yields the same
   observable tool-call events and results as inline execution in this
   controlled setup.
"""
import asyncio
import json
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel
from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import ToolResultEndEvent, ToolResultStartEvent
from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, ToolResponse, Toolkit

# Local alias used for the background-drain queue so the type parameter
# is explicit and no type: ignore comments are needed on queue operations.
ToolItem = ToolChunk | ToolResponse


# ---------------------------------------------------------------------------
# Sentinel type — gives the background queue a clean union type instead of
# relying on a bare `object()`, eliminating Queue[object] and type: ignore.
# ---------------------------------------------------------------------------

class _Sentinel:
    """End-of-stream marker enqueued by _drain() when next_handler is done."""


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class _EchoParams(BaseModel):
    """Parameters for EchoTool."""

    value: str


class EchoTool(ToolBase):
    """A minimal tool that records every call and echoes its input."""

    name: str = "echo"
    description: str = "Echo the value parameter."
    input_schema: dict = _EchoParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, call_log: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._call_log = call_log

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(self, value: str) -> ToolChunk:  # type: ignore[override]
        self._call_log.append(value)
        return ToolChunk(
            content=[TextBlock(text=f"echo:{value}")],
        )


class _IsolationParams(BaseModel):
    """Parameters for StateInjectionCheckTool."""

    value: str


class StateInjectionCheckTool(ToolBase):
    """Tool that captures any extra kwargs to verify state is not injected.

    ``is_state_injected=False`` means the toolkit must NOT pass
    ``_agent_state`` as a kwarg.  By accepting ``**extra_kwargs`` and
    recording them, the isolation test can assert the kwarg never arrived,
    making the verification meaningful even under future refactors.
    """

    name: str = "isolation_check"
    description: str = "Check that no extra kwargs (e.g. _agent_state) arrive."
    input_schema: dict = _IsolationParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, extra_kwargs_log: list[dict], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._extra_kwargs_log = extra_kwargs_log

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(  # type: ignore[override]
        self,
        value: str,
        **extra_kwargs: Any,
    ) -> ToolChunk:
        self._extra_kwargs_log.append(extra_kwargs)
        return ToolChunk(
            content=[TextBlock(text=f"check:{value}")],
        )


def _make_tool_call(value: str, call_id: str = "call_1") -> ToolCallBlock:
    """Helper to create a ToolCallBlock for EchoTool."""
    return ToolCallBlock(
        id=call_id,
        name="echo",
        input=json.dumps({"value": value}),
    )


async def _run_tool_call(
    agent: Agent,
    tool_call: ToolCallBlock,
) -> list[Any]:
    """Drive agent._execute_tool_call and return all emitted events.

    Centralises the single protected-access call so the rest of each
    test reads only against public event types.  If the internal API
    is ever renamed this helper is the only place that needs updating.
    """
    events = []
    async for event in agent._execute_tool_call(tool_call):  # pylint: disable=protected-access
        events.append(event)
    return events


class BackgroundActingMiddlewareTest(IsolatedAsyncioTestCase):
    """Tests for structural properties of the on_acting hook."""

    async def asyncSetUp(self) -> None:
        self.mock_model = MockModel()
        self.call_log: list[str] = []
        self.toolkit = Toolkit(tools=[EchoTool(call_log=self.call_log)])

    # ------------------------------------------------------------------
    # 1. Short-circuit: middleware returns synthetic result, tool not called
    # ------------------------------------------------------------------

    async def test_on_acting_short_circuit_skips_real_tool(self) -> None:
        """Middleware can return a synthetic ToolResponse without forwarding
        to next_handler; the real tool must never be invoked."""

        class SyntheticActingMiddleware(MiddlewareBase):
            """Returns a synthetic ToolResponse and never calls next_handler.

            The middleware must yield a ToolResponse (not just ToolChunk)
            so that _execute_tool_call can complete the tool-call lifecycle
            and emit ToolResultEndEvent.
            """

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                tc = input_kwargs["tool_call"]
                yield ToolResponse(
                    id=tc.id,
                    content=[TextBlock(text="synthetic_result")],
                    state=ToolResultState.SUCCESS,
                    metadata={},
                )

        agent = Agent(
            name="agent",
            system_prompt="test",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[SyntheticActingMiddleware()],
        )

        events = await _run_tool_call(agent, _make_tool_call("original"))

        # Real tool was never called
        self.assertEqual(self.call_log, [])

        # The tool-call lifecycle completed: ToolResultEndEvent was emitted
        end_events = [e for e in events if isinstance(e, ToolResultEndEvent)]
        self.assertEqual(len(end_events), 1)
        self.assertEqual(end_events[0].state, ToolResultState.SUCCESS)

    # ------------------------------------------------------------------
    # 2. Chained on_acting middlewares observe in outer→inner order
    # ------------------------------------------------------------------

    async def test_chained_on_acting_middlewares_onion_order(self) -> None:
        """Two on_acting middlewares must wrap each other in LIFO (onion)
        order: outer middleware sees the call first and last."""

        order_log: list[str] = []

        class OuterActing(MiddlewareBase):
            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                order_log.append("outer_before")
                async for item in next_handler(**input_kwargs):
                    yield item
                order_log.append("outer_after")

        class InnerActing(MiddlewareBase):
            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                order_log.append("inner_before")
                async for item in next_handler(**input_kwargs):
                    yield item
                order_log.append("inner_after")

        agent = Agent(
            name="agent",
            system_prompt="test",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[OuterActing(), InnerActing()],
        )

        await _run_tool_call(agent, _make_tool_call("x"))

        self.assertEqual(
            order_log,
            ["outer_before", "inner_before", "inner_after", "outer_after"],
        )
        # Real tool was reached through both middlewares
        self.assertEqual(self.call_log, ["x"])

    # ------------------------------------------------------------------
    # 3. Background task: draining next_handler via asyncio.Task
    # ------------------------------------------------------------------

    async def test_on_acting_next_handler_safe_as_background_task(
        self,
    ) -> None:
        """Draining next_handler via a background asyncio.Task yields the
        same observable tool-call events and results as inline execution.

        This test verifies the observable outcome (correct ToolResponse
        content and ToolResultStart/End events) — it does not make claims
        about internal state-mutation guarantees.
        """

        results_from_background: list[ToolItem] = []

        class BackgroundActingMiddleware(MiddlewareBase):
            """Spawns next_handler as a background task, drains the queue,
            then yields all collected items in order.

            Design notes:
            - Sentinel is enqueued in a finally block so errors in
              next_handler never deadlock the consumer loop.
            - Consumer loop is wrapped in try/finally so the background
              task is always cancelled and awaited if the generator is
              abandoned, preventing 'task destroyed but pending' warnings.
            - Queue is typed as Queue[ToolItem | _Sentinel] to avoid
              type: ignore on append/yield operations.
            """

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                queue: asyncio.Queue[ToolItem | _Sentinel] = asyncio.Queue()
                sentinel = _Sentinel()

                async def _drain() -> None:
                    try:
                        async for item in next_handler(**input_kwargs):
                            await queue.put(item)
                    finally:
                        # Always enqueue sentinel so the consumer is never
                        # left waiting on queue.get() if next_handler raises.
                        await queue.put(sentinel)

                task = asyncio.create_task(_drain())

                try:
                    while True:
                        item = await queue.get()
                        if isinstance(item, _Sentinel):
                            break
                        results_from_background.append(item)
                        yield item
                finally:
                    # If the consumer generator is abandoned before reaching
                    # the sentinel (e.g. due to an outer exception), cancel
                    # and await _drain to avoid 'task destroyed but pending'.
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass  # we cancelled it ourselves — expected
                    # Re-raise any real exception from _drain so the test
                    # fails deterministically on actual tool errors rather
                    # than silently passing with incomplete results.
                    if task.done() and not task.cancelled():
                        task.result()

        agent = Agent(
            name="agent",
            system_prompt="test",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[BackgroundActingMiddleware()],
        )

        outer_events = await _run_tool_call(agent, _make_tool_call("bg_test"))

        # Real tool was called exactly once
        self.assertEqual(self.call_log, ["bg_test"])

        # Background task collected ToolChunk + ToolResponse from call_tool
        self.assertGreaterEqual(len(results_from_background), 1)
        bg_responses = [
            r for r in results_from_background if isinstance(r, ToolResponse)
        ]
        self.assertEqual(len(bg_responses), 1)

        # Outer _execute_tool_call emitted start → streaming → end events
        start_events = [
            e for e in outer_events if isinstance(e, ToolResultStartEvent)
        ]
        end_events = [
            e for e in outer_events if isinstance(e, ToolResultEndEvent)
        ]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(len(end_events), 1)

        # The background ToolResponse carries the echoed text
        all_text = " ".join(
            b.text
            for b in bg_responses[0].content
            if isinstance(b, TextBlock)
        )
        self.assertIn("echo:bg_test", all_text)

    # ------------------------------------------------------------------
    # 4. on_acting is called independently for each tool call in a sequence
    # ------------------------------------------------------------------

    async def test_on_acting_called_per_tool_call(self) -> None:
        """When the agent executes two tool calls in a conversation turn,
        on_acting must be invoked once per call with the correct inputs."""

        intercepted: list[str] = []

        class RecordingMiddleware(MiddlewareBase):
            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                tc = input_kwargs["tool_call"]
                intercepted.append(json.loads(tc.input)["value"])
                async for item in next_handler(**input_kwargs):
                    yield item

        agent = Agent(
            name="agent",
            system_prompt="test",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[RecordingMiddleware()],
        )

        for val, cid in [("first", "c1"), ("second", "c2")]:
            await _run_tool_call(agent, _make_tool_call(val, cid))

        self.assertEqual(intercepted, ["first", "second"])
        self.assertEqual(self.call_log, ["first", "second"])

    # ------------------------------------------------------------------
    # 5. on_acting does not inject agent.state — confirms I/O isolation
    # ------------------------------------------------------------------

    async def test_on_acting_tool_not_injected_with_agent_state(
        self,
    ) -> None:
        """A tool with is_state_injected=False must not receive _agent_state
        as a keyword argument.

        StateInjectionCheckTool accepts **extra_kwargs and records them so
        that any unexpected injection (e.g. from a future refactor that
        accidentally passes _agent_state) will be caught by the assertion,
        making the test meaningfully verify isolation rather than just
        confirming that the call_log contains the expected value string.
        """
        extra_kwargs_log: list[dict] = []
        isolation_tool = StateInjectionCheckTool(
            extra_kwargs_log=extra_kwargs_log,
        )
        toolkit = Toolkit(tools=[isolation_tool])

        self.mock_model.set_responses(
            [ChatResponse(content=[TextBlock(text="ok")], is_last=True)]
        )
        agent = Agent(
            name="agent",
            system_prompt="secret system prompt",
            model=self.mock_model,
            toolkit=toolkit,
        )

        tool_call = ToolCallBlock(
            id="iso_1",
            name="isolation_check",
            input=json.dumps({"value": "probe"}),
        )
        await _run_tool_call(agent, tool_call)

        # Tool was called exactly once
        self.assertEqual(len(extra_kwargs_log), 1)

        # _agent_state must NOT have been injected because
        # StateInjectionCheckTool.is_state_injected = False
        self.assertNotIn(
            "_agent_state",
            extra_kwargs_log[0],
            "Toolkit must not inject _agent_state when is_state_injected=False",
        )
