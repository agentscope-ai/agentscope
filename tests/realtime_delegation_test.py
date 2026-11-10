# -*- coding: utf-8 -*-
"""Test Live delegation deduplication, result validation, and recovery."""
# pylint: disable=protected-access

import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    ConfirmResult,
    CustomEvent,
    ExternalExecutionResultEvent,
    ToolResultEndEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse
from agentscope.realtime import OpenAILiveModel
from agentscope.state import AgentState
from agentscope.tool import AskUser, FunctionTool, Toolkit


def _create_live_agent(model: MockModel, toolkit: Toolkit) -> RealtimeAgent:
    """Build a Live agent backed by a real Agent and a deterministic model."""

    def factory(state: AgentState) -> Agent:
        """Use real permission checks and external-result validation."""
        return Agent(
            "Friday",
            "Be brief.",
            model,
            state=state,
            toolkit=toolkit,
        )

    live_model = OpenAILiveModel(
        "gpt-live-1",
        OpenAICredential(api_key="test"),
    )
    live_model.send_delegation_result = AsyncMock()
    return RealtimeAgent(
        "Friday",
        "Be brief.",
        live_model,
        delegation_agent_factory=factory,
    )


class RealtimeDelegationDedupTest(IsolatedAsyncioTestCase):
    """Exercise concurrent approvals and external results with real Agents."""

    async def _start(
        self,
        external: bool,
        call_ids: list[str],
    ) -> tuple[RealtimeAgent, Agent, list[str]]:
        """Create one parked delegation and record actual tool executions."""
        executed: list[str] = []

        def write(value: str) -> str:
            """Record each approved execution."""
            executed.append(value)
            return value

        tool = FunctionTool(write, is_read_only=external)
        tool.is_external_tool = external
        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=call_id,
                            name="write",
                            input=json.dumps({"value": call_id}),
                        )
                        for call_id in call_ids
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="Done.")], is_last=True),
            ],
        )

        live = _create_live_agent(model, Toolkit(tools=[tool]))
        self.addAsyncCleanup(live.close)
        live._start_client_delegation("task")
        await asyncio.gather(*list(live._tasks))
        backend = live._delegations["task"].agent
        assert backend is not None
        self.assertEqual(
            [tc.id for tc in backend.state.get_awaiting_tool_calls("Friday")],
            call_ids if external else call_ids[:1],
        )
        return live, backend, executed

    @staticmethod
    def _input(
        backend: Agent,
        external: bool,
        call_ids: list[str],
    ) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
        """Build a response from the original calls, including stale ones."""
        reply = backend.state.get_reply_message("Friday")
        assert reply is not None
        calls = [
            tc.model_copy(deep=True)
            for tc in reply.get_content_blocks("tool_call")
            if tc.id in call_ids
        ]
        if external:
            return ExternalExecutionResultEvent(
                reply_id=reply.id,
                execution_results=[
                    ToolResultBlock(
                        id=tc.id,
                        name=tc.name,
                        output=tc.id,
                        state="success",
                    )
                    for tc in calls
                ],
            )
        return UserConfirmResultEvent(
            reply_id=reply.id,
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=tc) for tc in calls
            ],
        )

    async def test_duplicates_during_execution_and_result_send(self) -> None:
        """A queued duplicate cannot send failure after the success result."""
        for external in (False, True):
            with self.subTest(external=external):
                live, backend, executed = await self._start(external, ["one"])
                sending = asyncio.Event()
                release = asyncio.Event()

                async def send_result(
                    _delegation_id: str,
                    _content: str,
                    started: asyncio.Event = sending,
                    finish: asyncio.Event = release,
                ) -> None:
                    """Expose the window after execution and before removal."""
                    started.set()
                    await finish.wait()

                sender = AsyncMock(side_effect=send_result)
                live.model.send_delegation_result = sender
                event = self._input(backend, external, ["one"])
                with patch.object(
                    backend,
                    "reply_stream",
                    wraps=backend.reply_stream,
                ) as reply_stream:
                    try:
                        await live.send(event)
                        await live.send(event.model_copy(deep=True))
                        await asyncio.wait_for(sending.wait(), timeout=1)
                        await live.send(event.model_copy(deep=True))
                    finally:
                        release.set()
                        await asyncio.gather(*list(live._tasks))
                    self.assertEqual(reply_stream.call_count, 1)
                self.assertEqual(
                    {
                        "results": sender.await_args_list,
                        "executed": executed,
                        "delegations": live._delegations,
                        "routes": live._delegated_tool_calls,
                    },
                    {
                        "results": [call("task", "Done.")],
                        "executed": [] if external else ["one"],
                        "delegations": {},
                        "routes": {},
                    },
                )

    async def test_duplicates_preserve_other_pending_tools(self) -> None:
        """Skip only completed inputs, retaining new decisions in a batch."""
        for external in (False, True):
            with self.subTest(external=external):
                live, backend, executed = await self._start(
                    external,
                    ["one", "two"],
                )
                sender = live.model.send_delegation_result
                event = self._input(backend, external, ["one"])
                await live.send(event)
                await live.send(event.model_copy(deep=True))
                await asyncio.gather(*list(live._tasks))
                self.assertEqual(
                    [
                        tc.id
                        for tc in live.state.get_awaiting_tool_calls("Friday")
                    ],
                    ["two"],
                )
                delegation = live._delegations["task"]
                self.assertIsNotNone(delegation.timeout_task)
                self.assertFalse(delegation.timeout_task.done())
                timeout = delegation.timeout_task
                await live.send(event.model_copy(deep=True))
                await asyncio.gather(*list(live._tasks))
                self.assertIs(delegation.timeout_task, timeout)
                self.assertFalse(timeout.done())
                sender.assert_not_awaited()
                await live.send(self._input(backend, external, ["one", "two"]))
                await asyncio.gather(*list(live._tasks))
                reply = live.state.get_reply_message("Friday")
                assert reply is not None
                self.assertEqual(
                    {
                        "results": sender.await_args_list,
                        "executed": executed,
                        "tool_results": [
                            (block.id, block.state)
                            for block in reply.get_content_blocks(
                                "tool_result",
                            )
                        ],
                        "pending": live.state.get_awaiting_tool_calls(
                            "Friday",
                        ),
                    },
                    {
                        "results": [call("task", "Done.")],
                        "executed": [] if external else ["one", "two"],
                        "tool_results": [
                            ("one", "success"),
                            ("two", "success"),
                        ],
                        "pending": [],
                    },
                )


class RealtimeDelegationErrorTest(IsolatedAsyncioTestCase):
    """Exercise validation and cleanup with a real Agent and AskUser."""

    async def test_model_failure_preserves_completed_tool_history(
        self,
    ) -> None:
        """Keep successful tool records when subsequent reasoning fails."""
        executed: list[str] = []

        def write(value: str) -> str:
            """Record one execution before the model fails."""
            executed.append(value)
            return value

        for needs_confirmation in (False, True):
            with self.subTest(needs_confirmation=needs_confirmation):
                executed.clear()
                model = MockModel(context_size=10000)
                model.set_responses(
                    [
                        ChatResponse(
                            content=[
                                ToolCallBlock(
                                    id="write-1",
                                    name="write",
                                    input=json.dumps({"value": "saved"}),
                                ),
                            ],
                            is_last=True,
                        ),
                        RuntimeError("Model failed after tool execution."),
                    ],
                )
                live = _create_live_agent(
                    model,
                    Toolkit(
                        tools=[
                            FunctionTool(
                                write,
                                is_read_only=not needs_confirmation,
                            ),
                        ],
                    ),
                )
                self.addAsyncCleanup(live.close)
                live._start_client_delegation("task")
                backend = live._delegations["task"].agent
                assert backend is not None
                if needs_confirmation:
                    await asyncio.gather(*list(live._tasks))
                    pending = backend.state.get_awaiting_tool_calls("Friday")
                    await live.send(
                        UserConfirmResultEvent(
                            reply_id=backend.state.reply_id,
                            confirm_results=[
                                ConfirmResult(
                                    confirmed=True,
                                    tool_call=pending[0],
                                ),
                            ],
                        ),
                    )
                with self.assertLogs("as", level="ERROR"):
                    await asyncio.gather(*list(live._tasks))
                reply = backend.state.get_reply_message("Friday")
                assert reply is not None
                self.assertEqual(
                    {
                        "executed": executed,
                        "model_calls": model.cnt,
                        "results": [
                            (
                                block.id,
                                block.state,
                                [
                                    item.text
                                    for item in block.output
                                    if isinstance(item, TextBlock)
                                ],
                            )
                            for block in reply.get_content_blocks(
                                "tool_result",
                            )
                        ],
                        "active": live._active_delegation_id,
                        "delegations": live._delegations,
                        "routes": live._delegated_tool_calls,
                    },
                    {
                        "executed": ["saved"],
                        "model_calls": 2,
                        "results": [("write-1", "success", ["saved"])],
                        "active": None,
                        "delegations": {},
                        "routes": {},
                    },
                )
                expected = reply.model_copy(deep=True)
                expected.content = [
                    block
                    for block in expected.content
                    if block.type in {"tool_call", "tool_result"}
                ]
                expected_context = [expected.model_dump(mode="json")]
                self.assertEqual(
                    live.state.model_dump(mode="json")["context"],
                    expected_context,
                )
                live.model.send_delegation_result.assert_awaited_once_with(
                    "task",
                    "The delegated task failed. Ask the user to try again.",
                )
                await live.close()
                self.assertEqual(
                    live.state.model_dump(mode="json")["context"],
                    expected_context,
                )

    async def test_model_failure_publishes_accepted_external_result(
        self,
    ) -> None:
        """Publish a pending result END even when no next event is yielded."""
        live = await self._start_delegation(["last"])
        backend = live._delegations["task"].agent
        assert backend is not None
        backend.model.set_responses([RuntimeError("Model failed.")])
        result = self._result("last", True)
        with self.assertLogs("as", level="ERROR"):
            await live.send(
                ExternalExecutionResultEvent(
                    reply_id=backend.state.reply_id,
                    execution_results=[result],
                ),
            )
            await asyncio.gather(*list(live._tasks))
        self.assertEqual(
            [
                (event.tool_call_id, event.state)
                for event in live.drain_events()
                if isinstance(event, ToolResultEndEvent)
            ],
            [("last", "success")],
        )
        reply = live.state.get_reply_message("Friday")
        assert reply is not None
        self.assertEqual(
            reply.get_content_blocks("tool_result"),
            [result],
        )
        self.assertEqual(live._delegations, {})
        live.model.send_delegation_result.assert_awaited_once_with(
            "task",
            "The delegated task failed. Ask the user to try again.",
        )

    async def _start_delegation(self, call_ids: list[str]) -> RealtimeAgent:
        """Park the backend on one or more external questions."""
        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=call_id,
                            name="AskUser",
                            input=json.dumps(
                                {
                                    "questions": [
                                        {
                                            "question": f"Do {call_id}?",
                                            "header": "Approval",
                                            "options": [
                                                {
                                                    "label": "Yes",
                                                    "description": "Proceed",
                                                },
                                                {
                                                    "label": "No",
                                                    "description": "Stop",
                                                },
                                            ],
                                        },
                                    ],
                                },
                            ),
                        )
                        for call_id in call_ids
                    ],
                    is_last=True,
                ),
                ChatResponse(
                    content=[TextBlock(text="Answered.")],
                    is_last=True,
                ),
            ],
        )

        live = _create_live_agent(model, Toolkit(tools=[AskUser()]))
        self.addAsyncCleanup(live.close)
        live._start_client_delegation("task")
        await asyncio.gather(*list(live._tasks))
        self.assertEqual(
            [call.id for call in live.state.get_awaiting_tool_calls("Friday")],
            call_ids,
        )
        list(live.drain_events())
        return live

    @staticmethod
    def _result(call_id: str, valid: bool) -> ToolResultBlock:
        """Build an answer with or without the required metadata."""
        return ToolResultBlock(
            id=call_id,
            name="AskUser",
            output="Yes",
            state="success",
            metadata={
                "answers": [
                    {"question": f"Do {call_id}?", "selected": ["Yes"]},
                ],
            }
            if valid
            else {},
        )

    async def test_invalid_results_can_retry_close_or_expire(self) -> None:
        """Rejected answers retain routing, timeout, and partial results."""
        for action in ("retry", "close", "expire"):
            for partial in (False, True):
                with self.subTest(action=action, partial=partial):
                    call_ids = ["first", "last"] if partial else ["last"]
                    live = await self._start_delegation(call_ids)
                    send_result = live.model.send_delegation_result
                    delegation = live._delegations["task"]
                    assert delegation.agent is not None
                    old_timeout = delegation.timeout_task
                    results = [self._result("first", True)] if partial else []
                    results.append(self._result("last", False))
                    submission = ExternalExecutionResultEvent(
                        reply_id=live.state.reply_id,
                        execution_results=results,
                    )
                    with self.assertLogs("as", level="ERROR"):
                        await live.send(submission)
                        await asyncio.gather(*list(live._tasks))
                    events = list(live.drain_events())
                    self.assertEqual(
                        {
                            "active": live._active_delegation_id,
                            "routes": live._delegated_tool_calls,
                            "pending": [
                                call.id
                                for call in live.state.get_awaiting_tool_calls(
                                    "Friday",
                                )
                            ],
                            "timeout_rearmed": (
                                delegation.timeout_task is not None
                                and delegation.timeout_task is not old_timeout
                                and not delegation.timeout_task.done()
                            ),
                            "ends": [
                                (event.tool_call_id, event.state)
                                for event in events
                                if isinstance(event, ToolResultEndEvent)
                            ],
                            "reported": send_result.await_count,
                            "notifications": [
                                (event.name, event.value)
                                for event in events
                                if isinstance(event, CustomEvent)
                            ],
                        },
                        {
                            "active": "task",
                            "routes": dict.fromkeys(call_ids, "task"),
                            "pending": ["last"],
                            "timeout_rearmed": True,
                            "ends": [("first", "success")] if partial else [],
                            "reported": 0,
                            "notifications": [
                                (
                                    "external_execution_result_rejected",
                                    {
                                        "request_id": submission.id,
                                        "reply_id": submission.reply_id,
                                        "tool_call_ids": ["last"],
                                        "message": (
                                            "The submitted result was "
                                            "rejected. Please check your "
                                            "answer and submit again."
                                        ),
                                    },
                                ),
                            ],
                        },
                    )
                    if action == "retry":
                        await live.send(
                            ExternalExecutionResultEvent(
                                reply_id=live.state.reply_id,
                                execution_results=[self._result("last", True)],
                            ),
                        )
                        await asyncio.gather(*list(live._tasks))
                        send_result.assert_awaited_once_with(
                            "task",
                            "Answered.",
                        )
                    elif action == "close":
                        await live.close()
                        send_result.assert_not_awaited()
                    else:
                        timeout = delegation.timeout_task
                        live._cancel_delegation_timeout(delegation)
                        await asyncio.gather(timeout, return_exceptions=True)
                        with patch(
                            "agentscope.agent._realtime._agent."
                            "_DELEGATION_INPUT_TIMEOUT_SECS",
                            0,
                        ):
                            await live._expire_client_delegation(delegation)
                        send_result.assert_awaited_once()
                    reply = live.state.get_reply_message("Friday")
                    assert reply is not None
                    self.assertEqual(
                        [
                            event.name
                            for event in live.drain_events()
                            if isinstance(event, CustomEvent)
                        ],
                        [],
                    )
                    self.assertEqual(
                        {
                            "active": live._active_delegation_id,
                            "delegations": live._delegations,
                            "routes": live._delegated_tool_calls,
                            "pending": live.state.get_awaiting_tool_calls(
                                "Friday",
                            ),
                            "results": [
                                (block.id, block.state)
                                for block in reply.get_content_blocks(
                                    "tool_result",
                                )
                            ],
                        },
                        {
                            "active": None,
                            "delegations": {},
                            "routes": {},
                            "pending": [],
                            "results": (
                                [("first", "success")] if partial else []
                            )
                            + [
                                (
                                    "last",
                                    "success"
                                    if action == "retry"
                                    else "interrupted",
                                ),
                            ],
                        },
                    )
