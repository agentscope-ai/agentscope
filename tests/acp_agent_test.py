# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position,protected-access
"""Exercise ACP turns with real Agents and deterministic model responses."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import anyio
import pytest

pytest.importorskip("acp", reason="ACP tests require the acp extra")

from acp import PROTOCOL_VERSION, RequestError, text_block
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    RequestPermissionResponse,
    ResourceContentBlock,
    SseMcpServer,
)
from utils import MockModel

from agentscope.acp import ACPAgentServer, ACPSessionContext
from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    RequireUserConfirmEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.acp._events import EventProjector
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
)
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.types import ErrorInfo, ReplyFinishedReason


class _Tool(ToolBase):
    """Record execution, optionally blocking until cancelled."""

    name = "Record"
    description = "Record a value."
    input_schema = {"type": "object", "properties": {}}
    is_read_only = False
    is_concurrency_safe = True
    is_external_tool = False
    is_mcp = False

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.started = asyncio.Event()
        self.block = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always request an explicit decision."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Please approve",
        )

    async def __call__(self, **_: Any) -> AsyncIterator[ToolChunk]:
        """Emit a real tool result or wait for interruption."""
        self.calls += 1
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        yield ToolChunk(content=[TextBlock(text="recorded")], is_last=True)


class _Client:
    """Capture complete ACP updates and control permission decisions."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.permissions: list[dict[str, Any]] = []
        self.requested = asyncio.Event()
        self.wait = False
        self.option: str | None = "allow_once"

    async def session_update(self, session_id: str, update: Any) -> None:
        """Store wire-shaped snapshots."""
        self.updates.append(
            {
                "session": session_id,
                "update": update.model_dump(by_alias=True, exclude_none=True),
            },
        )

    async def request_permission(self, **kwargs: Any) -> Any:
        """Wait when requested, otherwise return a configured option."""
        self.permissions.append(kwargs)
        self.requested.set()
        if self.wait:
            await asyncio.Event().wait()
        outcome = (
            AllowedOutcome(outcome="selected", option_id=self.option)
            if self.option is not None
            else DeniedOutcome(outcome="cancelled")
        )
        return RequestPermissionResponse(outcome=outcome)


class ACPAgentTest(IsolatedAsyncioTestCase):
    """Verify lifecycle semantics independently of stdio transport."""

    # pylint: disable=too-many-public-methods

    async def asyncSetUp(self) -> None:
        # unittest cleanup owns the directory for the whole test.
        # pylint: disable-next=consider-using-with
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tools: list[_Tool] = []
        self.agents: list[Agent] = []
        self.closed: list[str] = []
        self.factory_exit_error = False
        self.owner_tasks: list[asyncio.Task] = []

        @asynccontextmanager
        async def factory(ctx: ACPSessionContext) -> AsyncIterator[Agent]:
            tool = _Tool()
            model = MockModel()
            model.set_responses(
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="call",
                                name="Record",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="done")],
                        is_last=True,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="next")],
                        is_last=True,
                    ),
                ],
            )
            agent = Agent(
                name="test",
                system_prompt="Test agent",
                model=model,
                toolkit=Toolkit(tools=[tool]),
            )
            self.tools.append(tool)
            self.agents.append(agent)
            owner = asyncio.current_task()
            assert owner is not None
            self.owner_tasks.append(owner)
            try:
                yield agent
            finally:
                self.assertIs(asyncio.current_task(), owner)
                self.closed.append(ctx.session_id)
                if self.factory_exit_error:
                    raise RuntimeError("factory exit failed")

        self.client = _Client()
        self.server = ACPAgentServer(factory)
        self.server.on_connect(self.client)
        await self.server.initialize(PROTOCOL_VERSION)
        self.addAsyncCleanup(self.server.aclose)

    async def session(self) -> str:
        """Create an isolated session in a real temporary directory."""
        response = await self.server.new_session(self.directory.name)
        return response.session_id

    async def send(self, sid: str) -> Any:
        """Send a bounded prompt so regressions fail instead of hanging."""
        return await asyncio.wait_for(
            self.server.prompt([text_block("hello")], sid),
            timeout=5,
        )

    async def test_approval_runs_once_and_streams_complete_tool_state(
        self,
    ) -> None:
        """The same ACP prompt continues after approval and preserves state."""
        sid = await self.session()
        response = await self.send(sid)
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(self.tools[0].calls, 1)
        self.assertEqual(
            [item["update"] for item in self.client.updates],
            [
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call",
                    "title": "Record",
                    "status": "pending",
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call",
                    "rawInput": {},
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call",
                    "status": "in_progress",
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call",
                    "rawOutput": "recorded",
                    "content": [
                        {
                            "type": "content",
                            "content": {"type": "text", "text": "recorded"},
                        },
                    ],
                },
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call",
                    "status": "completed",
                    "rawOutput": "recorded",
                    "content": [
                        {
                            "type": "content",
                            "content": {"type": "text", "text": "recorded"},
                        },
                    ],
                },
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "done"},
                },
            ],
        )
        await self.send(sid)
        self.assertEqual(self.tools[0].calls, 1)
        self.assertEqual(
            [
                m.get_text_content()
                for m in self.agents[0].state.context
                if m.role == "user"
            ],
            ["hello", "hello"],
        )

    async def test_rejection_and_unknown_option_never_execute(self) -> None:
        """Reject invalid selection IDs instead of treating them as grants."""
        for choice in ("reject_once", "unknown"):
            self.client.option = choice
            sid = await self.session()
            self.assertEqual((await self.send(sid)).stop_reason, "end_turn")
            self.assertEqual(self.tools[-1].calls, 0)
            await self.server.close_session(sid)

    async def test_cancel_waiting_permission_cleans_parked_agent(self) -> None:
        """No pending call survives cancellation; next turn is usable."""
        sid = await self.session()
        self.client.wait = True
        task = asyncio.create_task(self.send(sid))
        await asyncio.wait_for(self.client.requested.wait(), 3)
        with self.assertRaises(RequestError) as error:
            await self.send(sid)
        self.assertEqual(error.exception.code, -32600)
        await self.server.cancel(sid)
        self.assertEqual((await task).stop_reason, "cancelled")
        self.assertEqual(self.tools[0].calls, 0)
        self.client.wait = False
        self.assertEqual((await self.send(sid)).stop_reason, "end_turn")
        self.assertEqual(len(self.client.permissions), 1)

    async def test_cancel_running_tool_and_close(self) -> None:
        """Shutdown waits for interrupted execution before factory exit."""
        sid = await self.session()
        self.tools[0].block = True
        task = asyncio.create_task(self.send(sid))
        await asyncio.wait_for(self.tools[0].started.wait(), 3)
        await self.server.close_session(sid)
        self.assertEqual((await task).stop_reason, "cancelled")
        self.assertEqual(self.closed, [sid])
        self.assertEqual(self.server._sessions, {})

    async def test_cancel_after_approval_before_tool_start(self) -> None:
        """Interrupted ALLOWED calls need results without a START event."""
        for raise_cancelled in (False, True):
            with self.subTest(raise_cancelled=raise_cancelled):
                await self._check_cancel_before_start(raise_cancelled)

    async def _check_cancel_before_start(self, raise_cancelled: bool) -> None:
        """Validate cancellation and the next provider-formatted request."""
        sid = await self.session()
        agent = self.agents[-1]
        agent.react_config.interruption_raise_cancelled_error = raise_cancelled
        approved = asyncio.Event()
        original = Agent._check_permission

        async def permission(
            current: Agent,
            call: ToolCallBlock,
            tool: ToolBase,
            tool_input: dict[str, Any],
        ) -> PermissionDecision:
            if call.state == ToolCallState.ALLOWED:
                approved.set()
                await asyncio.Event().wait()
            return await original(current, call, tool, tool_input)

        with patch.object(Agent, "_check_permission", permission):
            prompt = asyncio.create_task(self.send(sid))
            await asyncio.wait_for(approved.wait(), 3)
            await self.server.cancel(sid)
            self.assertEqual((await prompt).stop_reason, "cancelled")
        self.assertEqual(self.tools[-1].calls, 0)
        updates = [
            item["update"]
            for item in self.client.updates
            if item["session"] == sid
        ]
        self.assertEqual(
            [item["status"] for item in updates if "status" in item],
            ["pending", "failed"],
        )
        calls = [
            block
            for msg in agent.state.context
            for block in msg.content
            if isinstance(block, ToolCallBlock)
        ]
        results = [
            block
            for msg in agent.state.context
            for block in msg.content
            if isinstance(block, ToolResultBlock)
        ]
        self.assertEqual(
            [(call.id, call.state) for call in calls],
            [
                ("call", ToolCallState.FINISHED),
            ],
        )
        self.assertEqual(
            [result.model_dump(exclude={"created_at"}) for result in results],
            [
                ToolResultBlock(
                    id="call",
                    name="Record",
                    state="interrupted",
                    output=(
                        "<system-reminder>The tool call has been interrupted "
                        "by the user.</system-reminder>"
                    ),
                ).model_dump(exclude={"created_at"}),
            ],
        )
        original_api = MockModel._call_api

        async def checked_api(
            model: MockModel,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            messages = await model.formatter.format(kwargs["messages"])
            self.assertEqual(
                (
                    [
                        call["id"]
                        for msg in messages
                        for call in msg.get("tool_calls", [])
                    ],
                    [
                        msg["tool_call_id"]
                        for msg in messages
                        if msg["role"] == "tool"
                    ],
                ),
                (["call"], ["call"]),
            )
            return await original_api(model, *args, **kwargs)

        with patch.object(MockModel, "_call_api", checked_api):
            self.assertEqual((await self.send(sid)).stop_reason, "end_turn")
        await self.server.close_session(sid)

    async def test_session_mcp_cleanup_preserves_equal_factory_client(
        self,
    ) -> None:
        """Value-equal clients still belong to different resource owners."""
        existing = MCPClient(
            name="same",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="https://example.com/mcp"),
        )
        added = MCPClient(**existing.model_dump())
        self.assertEqual(existing, added)
        agent = Agent(
            name="test",
            system_prompt="test",
            model=MockModel(),
            toolkit=Toolkit(mcps=[existing]),
        )

        @asynccontextmanager
        async def factory(_: ACPSessionContext) -> AsyncIterator[Agent]:
            yield agent

        server = ACPAgentServer(factory)
        await server.initialize(PROTOCOL_VERSION)
        with patch(
            "agentscope.acp._server.build_mcp_clients",
            return_value=[added],
        ):
            response = await server.new_session(self.directory.name)
        await server.close_session(response.session_id)
        self.assertEqual(len(agent.toolkit.tool_groups[0].mcps), 1)
        self.assertIs(agent.toolkit.tool_groups[0].mcps[0], existing)

    async def test_close_cancellation_survives_factory_failure(self) -> None:
        """Cleanup errors are chained without changing task cancellation."""
        sid = await self.session()
        self.factory_exit_error = True
        entered = asyncio.Event()

        async def cancel(_: str) -> None:
            entered.set()
            await asyncio.Event().wait()

        try:
            with patch.object(self.server, "cancel", cancel):
                task = asyncio.create_task(self.server.close_session(sid))
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError) as error:
                    await task
            self.assertTrue(task.cancelled())
            self.assertIsInstance(
                error.exception.__cause__,
                BaseExceptionGroup,
            )
            self.assertEqual(
                [str(exc) for exc in error.exception.__cause__.exceptions],
                ["factory exit failed"],
            )
            self.assertEqual(self.closed, [sid])
            self.assertEqual(self.server._sessions, {})
        finally:
            self.factory_exit_error = False

    async def test_cancellation_while_owner_exits_is_preserved(self) -> None:
        """Cancellation during factory exit waits for cleanup and propagates."""
        entered, release = asyncio.Event(), asyncio.Event()
        original_factory = self.server._factory

        @asynccontextmanager
        async def factory(ctx: ACPSessionContext) -> AsyncIterator[Agent]:
            async with original_factory(ctx) as agent:
                yield agent
                entered.set()
                await release.wait()

        self.server._factory = factory
        sid = await self.session()
        self.factory_exit_error = True
        try:
            task = asyncio.create_task(self.server.close_session(sid))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError) as error:
                await task
            self.assertTrue(task.cancelled())
            self.assertEqual(
                [str(exc) for exc in error.exception.__cause__.exceptions],
                ["factory exit failed"],
            )
            self.assertEqual(self.closed, [sid])
            self.assertEqual(self.server._sessions, {})
        finally:
            release.set()
            self.factory_exit_error = False

    async def test_cancelled_close_waits_for_tool_cleanup(self) -> None:
        """Caller cancellation must not close resources under a running tool."""
        sid = await self.session()
        started, cleaning, release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def execute(_: _Tool, **__: Any) -> AsyncIterator[ToolChunk]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
            yield ToolChunk(content=[], is_last=True)

        with patch.object(_Tool, "__call__", execute):
            prompt = asyncio.create_task(self.send(sid))
            await asyncio.wait_for(started.wait(), 3)
            close = asyncio.create_task(self.server.close_session(sid))
            try:
                await asyncio.wait_for(cleaning.wait(), 3)
                close.cancel()
                await asyncio.sleep(0)
                self.assertEqual(self.closed, [])
                self.assertFalse(close.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await close
            self.assertEqual((await prompt).stop_reason, "cancelled")
        self.assertEqual(self.closed, [sid])
        self.assertEqual(self.server._sessions, {})

    async def test_factory_exit_has_a_deadline(self) -> None:
        """Native timeout cancellation interrupts an AnyIO-shielded exit."""
        for mode in ("close", "disconnect", "cancelled_close", "setup"):
            with self.subTest(mode=mode):
                await self._check_factory_exit_deadline(mode)

    async def _check_factory_exit_deadline(self, mode: str) -> None:
        """Keep a watchdog so missing timeout logic fails without hanging."""
        entered, release = asyncio.Event(), asyncio.Event()
        original_factory = self.server._factory

        @asynccontextmanager
        async def factory(ctx: ACPSessionContext) -> AsyncIterator[Agent]:
            async with original_factory(ctx) as agent:
                # AnyIO shielding does not block native task cancellation.
                # This factory propagates it; swallowing it is unsupported.
                with anyio.CancelScope(shield=True):
                    try:
                        yield agent
                    finally:
                        entered.set()
                        await release.wait()

        class FailedMCP:
            """Fail after the factory has entered, before session readiness."""

            async def connect(self) -> None:
                """Trigger rollback through the factory's exit."""
                raise RuntimeError("MCP setup failed")

        server = ACPAgentServer(factory)
        await server.initialize(PROTOCOL_VERSION)
        watchdog = asyncio.get_running_loop().call_later(1, release.set)
        try:
            with (
                patch(
                    "agentscope.acp._server._FACTORY_CLOSE_TIMEOUT",
                    0.02,
                ),
                patch("agentscope.acp._server.logger.warning") as warning,
            ):
                if mode == "setup":
                    with patch(
                        "agentscope.acp._server.build_mcp_clients",
                        return_value=[FailedMCP()],
                    ):
                        with self.assertRaisesRegex(
                            TimeoutError,
                            "factory cleanup exceeded",
                        ) as error:
                            await server.new_session(self.directory.name)
                    self.assertEqual(
                        str(error.exception.__context__),
                        "MCP setup failed",
                    )
                else:
                    response = await server.new_session(self.directory.name)
                    task = asyncio.create_task(
                        server.aclose()
                        if mode == "disconnect"
                        else server.close_session(response.session_id),
                    )
                    await asyncio.wait_for(entered.wait(), 1)
                    if mode == "cancelled_close":
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError) as err:
                            await task
                        self.assertTrue(task.cancelled())
                        self.assertEqual(
                            [
                                type(exc)
                                for exc in err.exception.__cause__.exceptions
                            ],
                            [TimeoutError],
                        )
                    else:
                        with self.assertRaisesRegex(
                            TimeoutError,
                            "factory cleanup exceeded",
                        ):
                            await task
                self.assertFalse(release.is_set())
                self.assertEqual(server._sessions, {})
                self.assertTrue(self.owner_tasks[-1].done())
                self.assertEqual(len(self.closed), len(self.agents))
                warning.assert_called_once_with(
                    f"ACP session '{self.closed[-1]}' factory cleanup "
                    f"exceeded 0.02 seconds; cleanup was interrupted and "
                    f"resources may not be fully released.",
                )
        finally:
            release.set()
            watchdog.cancel()
            await server.aclose()

    async def test_factory_timeout_error_is_preserved(self) -> None:
        """An application timeout must not be relabelled as our deadline."""
        original_factory = self.server._factory
        failure = TimeoutError("application timeout")

        @asynccontextmanager
        async def factory(ctx: ACPSessionContext) -> AsyncIterator[Agent]:
            async with original_factory(ctx) as agent:
                yield agent
                raise failure

        self.server._factory = factory
        sid = await self.session()
        with patch("agentscope.acp._server.logger.warning") as warning:
            with self.assertRaises(TimeoutError) as error:
                await self.server.close_session(sid)
            warning.assert_not_called()
        self.assertIs(error.exception, failure)
        self.assertEqual(self.server._sessions, {})
        self.assertEqual(self.closed, [sid])

    async def test_cancelled_permission_response_aborts_turn(self) -> None:
        """Client cancellation differs from rejecting one tool."""
        self.client.option = None
        sid = await self.session()
        self.assertEqual((await self.send(sid)).stop_reason, "cancelled")
        self.assertEqual(self.tools[0].calls, 0)

    async def test_external_execution_is_rejected_and_unparked(self) -> None:
        """An unsupported tool cannot leave the Agent awaiting results."""
        sid = await self.session()
        self.tools[0].is_external_tool = True
        with self.assertRaises(RequestError) as error:
            await self.send(sid)
        self.assertEqual(error.exception.code, -32603)
        self.assertEqual((await self.send(sid)).stop_reason, "end_turn")
        self.assertEqual(self.tools[0].calls, 0)

    async def test_model_failure_is_an_rpc_error_and_session_can_retry(
        self,
    ) -> None:
        """Do not turn runtime errors into successful end_turn responses."""
        sid = await self.session()
        self.agents[0].model.set_responses([RuntimeError("model failed")])
        with self.assertRaises(RequestError) as error:
            await self.send(sid)
        self.assertEqual(error.exception.code, -32603)
        self.agents[0].model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="recovered")],
                    is_last=True,
                ),
            ],
        )
        self.assertEqual((await self.send(sid)).stop_reason, "end_turn")

    async def test_reply_error_details_are_preserved(self) -> None:
        """Structured reply errors survive the RPC adapter."""
        sid = await self.session()

        async def reply_stream(*_: Any) -> AsyncIterator[ReplyEndEvent]:
            yield ReplyEndEvent(
                session_id=sid,
                reply_id="reply",
                finished_reason=ReplyFinishedReason.ERROR,
                error=ErrorInfo(type="rate_limit", message="Retry later"),
            )

        with patch.object(Agent, "reply_stream", reply_stream):
            with self.assertRaises(RequestError) as error:
                await self.send(sid)
        self.assertEqual(
            (error.exception.code, error.exception.data),
            (
                -32603,
                {
                    "details": "Agent execution failed",
                    "error": {"type": "rate_limit", "message": "Retry later"},
                },
            ),
        )

    def test_long_tool_output_is_throttled(self) -> None:
        """A burst sends its first chunk and an unthrottled final snapshot."""
        projector = EventProjector()
        projector.project(
            ToolResultStartEvent(
                reply_id="reply",
                tool_call_id="call",
                tool_call_name="Record",
            ),
        )
        updates = []
        with patch("agentscope.acp._events.monotonic", return_value=0):
            for _ in range(1000):
                update = projector.project(
                    ToolResultTextDeltaEvent(
                        reply_id="reply",
                        tool_call_id="call",
                        delta="chunk",
                    ),
                )
                if update is not None:
                    updates.append(
                        update.model_dump(by_alias=True, exclude_none=True),
                    )
        self.assertEqual(
            updates,
            [
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call",
                    "content": [
                        {
                            "type": "content",
                            "content": {"type": "text", "text": "chunk"},
                        },
                    ],
                    "rawOutput": "chunk",
                },
            ],
        )
        update = projector.project(
            ToolResultEndEvent(
                reply_id="reply",
                tool_call_id="call",
                state="success",
            ),
        )
        self.assertEqual(
            update.model_dump(by_alias=True, exclude_none=True),
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call",
                "status": "completed",
                "content": [
                    {
                        "type": "content",
                        "content": {"type": "text", "text": "chunk" * 1000},
                    },
                ],
                "rawOutput": "chunk" * 1000,
            },
        )

    def test_tool_progress_is_throttled_per_call(self) -> None:
        """Interleaved calls and binary notices keep independent snapshots."""
        projector = EventProjector()
        updates = []
        with patch("agentscope.acp._events.monotonic") as clock:
            for timestamp, call_id, delta in (
                (0, "first", "a"),
                (0.1, "first", "b"),
                (0.2, "second", "x"),
                (1, "first", "c"),
                (1.1, "second", "y"),
            ):
                clock.return_value = timestamp
                update = projector.project(
                    ToolResultTextDeltaEvent(
                        reply_id="reply",
                        tool_call_id=call_id,
                        delta=delta,
                    ),
                )
                updates.append(update.raw_output if update else None)
            clock.return_value = 1.3
            update = projector.project(
                ToolResultDataDeltaEvent(
                    reply_id="reply",
                    tool_call_id="second",
                    media_type="image/png",
                    data="AA==",
                ),
            )
            updates.append(update.raw_output)
            for call_id in ("first", "second"):
                projector.project(
                    ToolResultEndEvent(
                        reply_id="reply",
                        tool_call_id=call_id,
                        state="success",
                    ),
                )
            update = projector.project(
                ToolResultTextDeltaEvent(
                    reply_id="reply",
                    tool_call_id="first",
                    delta="new",
                ),
            )
            updates.append(update.raw_output)
        self.assertEqual(
            updates,
            [
                "a",
                None,
                "x",
                "abc",
                None,
                "xy\n[Binary tool output: image/png]",
                "new",
            ],
        )

    async def test_tool_output_arrives_before_completion(self) -> None:
        """The ACP client sees output while the tool is still running."""
        sid = await self.session()
        received, release = asyncio.Event(), asyncio.Event()
        original_update = self.client.session_update

        async def execute(_: _Tool, **__: Any) -> AsyncIterator[ToolChunk]:
            yield ToolChunk(content=[TextBlock(text="first")], is_last=False)
            await release.wait()
            yield ToolChunk(content=[TextBlock(text="last")], is_last=True)

        async def observe(session_id: str, update: Any) -> None:
            await original_update(session_id, update)
            if getattr(update, "raw_output", None) == "first":
                received.set()

        with (
            patch.object(_Tool, "__call__", execute),
            patch.object(self.client, "session_update", observe),
            patch("agentscope.acp._events.monotonic", return_value=0),
        ):
            prompt = asyncio.create_task(self.send(sid))
            try:
                await asyncio.wait_for(received.wait(), 1)
                self.assertFalse(prompt.done())
            finally:
                release.set()
                result = await prompt
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertEqual(
            [
                (item["update"].get("status"), item["update"]["rawOutput"])
                for item in self.client.updates
                if "rawOutput" in item["update"]
            ],
            [(None, "first"), ("completed", "firstlast")],
        )

    async def test_sessions_have_independent_history(self) -> None:
        """Two sessions can execute simultaneously without shared state."""
        first, second = await self.session(), await self.session()
        await asyncio.gather(self.send(first), self.send(second))
        self.assertEqual([tool.calls for tool in self.tools], [1, 1])
        self.assertIsNot(self.agents[0].state, self.agents[1].state)
        await self.server.close_session(second)
        await self.server.close_session(first)

    async def test_resource_links_preserve_prompt_order(self) -> None:
        """Resource links reach the Agent with their name and URI intact."""
        sid = await self.session()
        response = await self.server.prompt(
            [
                text_block("Read this file"),
                ResourceContentBlock(
                    type="resource_link",
                    name="a.py",
                    uri="file:///tmp/a.py",
                ),
                text_block("and explain it"),
            ],
            sid,
        )
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(
            [
                msg.get_text_content()
                for msg in self.agents[0].state.context
                if msg.role == "user"
            ],
            ["Read this file\n[a.py](file:///tmp/a.py)\nand explain it"],
        )

    async def test_failed_factory_exit_removes_session(self) -> None:
        """A cleanup failure is reported once and never retains the session."""
        await self.session()

        @asynccontextmanager
        async def factory(_: ACPSessionContext) -> AsyncIterator[Agent]:
            yield self.agents[0]
            raise RuntimeError("factory exit failed")

        server = ACPAgentServer(factory)
        await server.initialize(PROTOCOL_VERSION)
        response = await server.new_session(self.directory.name)
        with self.assertRaisesRegex(RuntimeError, "factory exit failed"):
            await server.close_session(response.session_id)
        self.assertEqual(server._sessions, {})
        await server.close_session(response.session_id)
        await server.aclose()

    async def test_legacy_sse_is_rejected_before_factory(self) -> None:
        """HTTP declarations cannot bypass the advertised SSE restriction."""
        servers = [
            SseMcpServer(
                type="sse",
                name="legacy",
                url="https://example.com/mcp",
                headers=[],
            ),
            *[
                HttpMcpServer(
                    type="http",
                    name="legacy",
                    url=f"https://example.com{path}",
                    headers=[],
                )
                for path in (
                    "/sse",
                    "/sse?key=x",
                    "/messages/",
                    "/messages/?x",
                )
            ],
        ]
        for server in servers:
            with self.subTest(server=server):
                with self.assertRaises(RequestError) as error:
                    await self.server.new_session(
                        self.directory.name,
                        mcp_servers=[server],
                    )
                self.assertEqual(error.exception.code, -32602)
        self.assertEqual(self.agents, [])

    async def test_input_validation_does_not_create_or_mutate_state(
        self,
    ) -> None:
        """Reject invalid cwd, session IDs and unsupported content."""
        with self.assertRaises(RequestError):
            await self.server.new_session("relative")
        with self.assertRaises(RequestError):
            await self.send("missing")
        sid = await self.session()
        with self.assertRaises(RequestError):
            await self.server.prompt(
                [
                    ImageContentBlock(
                        type="image",
                        data="AA==",
                        mime_type="image/png",
                    ),
                ],
                sid,
            )
        self.assertEqual(self.agents[0].state.context, [])
        response = await self.server.initialize(PROTOCOL_VERSION + 1)
        self.assertEqual(response.protocol_version, PROTOCOL_VERSION)
        self.assertIsInstance(await self.session(), str)

    async def test_mcp_ownership_and_failed_setup_rollback(self) -> None:
        """MCP opens/closes in one task and setup failures release agents."""
        operations = []

        class FakeMCP:
            """Enforce task affinity like stateful MCP cancel scopes."""

            is_stateful = True

            async def connect(self) -> None:
                """Record the resource owner."""
                operations.append(("open", asyncio.current_task()))

            async def close(self) -> None:
                """Record the task releasing the resource."""
                operations.append(("close", asyncio.current_task()))

        with patch(
            "agentscope.acp._server.build_mcp_clients",
            return_value=[FakeMCP()],
        ):
            sid = await self.session()
        await self.server.close_session(sid)
        self.assertEqual(
            operations,
            [("open", self.owner_tasks[0]), ("close", self.owner_tasks[0])],
        )
        self.assertEqual(len(self.agents[0].toolkit.tool_groups), 1)

        async def fail_connect(_: Any) -> None:
            raise RuntimeError("MCP setup failed")

        with (
            patch(
                "agentscope.acp._server.build_mcp_clients",
                return_value=[FakeMCP()],
            ),
            patch.object(FakeMCP, "connect", fail_connect),
        ):
            with self.assertRaisesRegex(RuntimeError, "MCP setup failed"):
                await self.session()
        self.assertEqual(self.server._sessions, {})
        self.assertEqual(len(self.closed), 2)

    async def test_duplicate_mcp_names_rejected_before_factory(self) -> None:
        """Invalid configuration never starts an agent or subprocess."""
        server = McpServerStdio(name="same", command="unused", args=[], env=[])
        with self.assertRaises(RequestError):
            await self.server.new_session(
                str(Path(self.directory.name)),
                mcp_servers=[server, server],
            )
        self.assertEqual(self.agents, [])

    async def test_real_mcp_startup_failure_releases_factory(self) -> None:
        """A server exiting before initialization leaves no task scopes."""
        with self.assertRaisesRegex(Exception, "Connection closed"):
            await asyncio.wait_for(
                self.server.new_session(
                    self.directory.name,
                    mcp_servers=[
                        McpServerStdio(
                            name="broken",
                            command=sys.executable,
                            args=["-c", "raise SystemExit(0)"],
                            env=[],
                        ),
                    ],
                ),
                10,
            )
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(self.server._sessions, {})

    async def test_persistent_permission_preserves_suggested_rules(
        self,
    ) -> None:
        """Only offered persistent grants propagate into AgentScope rules."""
        self.client.option = "allow_always"
        call = ToolCallBlock(
            id="call",
            name="Record",
            input="{}",
            suggested_rules=[
                PermissionRule(
                    tool_name="Record",
                    rule_content=None,
                    behavior=PermissionBehavior.ALLOW,
                    source="suggested",
                ),
            ],
        )
        result = await self.server._confirm(
            "session",
            RequireUserConfirmEvent(reply_id="reply", tool_calls=[call]),
        )
        self.assertEqual(
            result.confirm_results,
            [
                ConfirmResult(
                    confirmed=True,
                    tool_call=call,
                    rules=call.suggested_rules,
                ),
            ],
        )
        call.suggested_rules = []
        result = await self.server._confirm(
            "session",
            RequireUserConfirmEvent(reply_id="reply", tool_calls=[call]),
        )
        self.assertEqual(
            result.confirm_results,
            [ConfirmResult(confirmed=False, tool_call=call, rules=None)],
        )

    async def test_cancel_before_prompt_task_starts_returns_response(
        self,
    ) -> None:
        """An immediate cancel still resolves the original ACP request."""
        sid = await self.session()
        task = asyncio.create_task(
            self.server.prompt([text_block("hello")], sid),
        )
        await asyncio.sleep(0)
        await self.server.cancel(sid)
        self.assertEqual((await task).stop_reason, "cancelled")
        self.assertEqual(self.tools[0].calls, 0)

    async def test_close_during_initialization_unblocks_request(self) -> None:
        """A disconnected client cannot leave a factory task behind."""
        entered = asyncio.Event()
        released = asyncio.Event()

        @asynccontextmanager
        async def factory(_: ACPSessionContext) -> AsyncIterator[Agent]:
            try:
                entered.set()
                await asyncio.Event().wait()
                yield self.agents[0]
            finally:
                released.set()

        server = ACPAgentServer(factory)
        await server.initialize(PROTOCOL_VERSION)
        task = asyncio.create_task(server.new_session(self.directory.name))
        await entered.wait()
        await asyncio.wait_for(server.aclose(), 3)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(released.is_set())
        self.assertEqual(server._sessions, {})
