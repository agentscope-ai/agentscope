# -*- coding: utf-8 -*-
"""Pending channel input invalidates automatic tool approval."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from itertools import product
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis

from channel_gateway_test import _channel_record, _ChannelStorage, _WM
from permission_review_test import _review, _tool
from service_scheduler_test import _make_bus
from service_inbox_middleware_test import _noop_next_handler
from utils import MockModel

from agentscope.state._state import ToolApproval
from agentscope.agent import Agent
from agentscope.app._bus_ops import (
    CHANNEL_USER_INBOX_SOURCE,
    deliver_to_inbox,
    deliver_channel_input,
    enqueue_chat_input,
    register_active_chat_reply,
    list_steering_chat_inputs,
    steer_chat_input,
    register_inbox_consumer,
    session_execution,
)
from agentscope.app.channel._base import ChannelEvent
from agentscope.app.channel._gateway import ChannelGateway
from agentscope.app.channel._routing import resolve
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    MessageBusKeys,
)
from agentscope.app.middleware import (
    InboxMiddleware,
    SteeringMiddleware as AppSteeringMiddleware,
)
from agentscope.app.workspace_manager import IsolationPolicy
from agentscope.event import (
    RequireUserConfirmEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import (
    HintBlock,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    UserMsg,
)
from agentscope.middleware import (
    MiddlewareBase,
    SteeringMiddleware,
    SteeringQueue,
)
from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionContext,
    PermissionMode,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


@asynccontextmanager
async def _bus(backend: str) -> AsyncGenerator[MessageBus, None]:
    """Use the real queue implementation for each supported backend."""
    async with AsyncExitStack() as stack:
        if backend == "memory":
            bus = await stack.enter_async_context(InMemoryMessageBus())
        else:
            redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            stack.push_async_callback(redis.aclose)
            bus = await stack.enter_async_context(_make_bus(redis))
        yield bus


class InboxApprovalGuardTest(IsolatedAsyncioTestCase):
    """Inbox inspection preserves delivery and never executes revoked work."""

    async def test_dispatch_observes_revocation_after_queue_checks(
        self,
    ) -> None:
        """Inputs block dispatch until the result start signals commitment."""
        for source, stage, interactive, backend in product(
            ("sdk", "steering", "inbox"),
            ("instructions", "start"),
            (True, False),
            ("memory", "redis"),
        ):
            with self.subTest(
                source=source,
                stage=stage,
                interactive=interactive,
                backend=backend,
            ):
                async with _bus(backend) as bus, AsyncExitStack() as stack:
                    queue = SteeringQueue()
                    calls = []

                    def edit() -> str:
                        calls.append(True)
                        return "done"

                    tool = FunctionTool(edit, is_read_only=False)
                    reviewer = AsyncMock(return_value=_review())
                    agent = Agent(
                        "agent",
                        "Help",
                        MockModel(),
                        toolkit=Toolkit(tools=[tool]),
                        middlewares=[
                            SteeringMiddleware(queue)
                            if source == "sdk"
                            else AppSteeringMiddleware(bus),
                            InboxMiddleware(bus),
                        ],
                        permission_reviewer=reviewer,
                    )
                    policy = agent.state.permission_context
                    policy.mode, policy.interactive = (
                        PermissionMode.AUTO,
                        interactive,
                    )
                    sid, rid = agent.state.session_id, agent.state.reply_id
                    await stack.enter_async_context(
                        session_execution(bus, sid),
                    )
                    await register_inbox_consumer(bus, sid)
                    await register_active_chat_reply(bus, sid, rid)
                    await queue.start_reply(sid, rid)

                    async def revoke() -> None:
                        if source == "sdk":
                            queue.submit(
                                UserMsg("user", "Stop."),
                                session_id=sid,
                                reply_id=rid,
                            )
                        elif source == "steering":
                            item = await enqueue_chat_input(
                                bus,
                                "user",
                                sid,
                                "agent",
                                UserMsg("user", "Stop."),
                            )
                            await steer_chat_input(
                                bus,
                                "user",
                                sid,
                                "agent",
                                item["id"],
                                rid,
                            )
                        else:
                            await deliver_channel_input(
                                bus,
                                session_id=sid,
                                hint=HintBlock(hint="Stop.", source="channel"),
                            )

                    loads = 0

                    async def instructions() -> str:
                        nonlocal loads
                        loads += 1
                        if stage == "instructions" and loads == 3:
                            await revoke()
                        return "Help"

                    outcomes = []
                    with patch.object(
                        agent,
                        "_get_permission_instructions",
                        instructions,
                    ):
                        async for event in agent._execute_tool_call(
                            ToolCallBlock(
                                id="edit",
                                name=tool.name,
                                input="{}",
                            ),
                        ):
                            if (
                                isinstance(event, ToolResultStartEvent)
                                and stage == "start"
                            ):
                                await revoke()
                            if isinstance(event, RequireUserConfirmEvent):
                                outcomes.append("ask")
                            elif isinstance(event, ToolResultEndEvent):
                                outcomes.append(event.state)
                    if source == "sdk":
                        pending = await queue.read(sid, rid)
                    elif source == "steering":
                        pending = await list_steering_chat_inputs(
                            bus,
                            sid,
                            rid,
                        )
                    else:
                        pending = await bus.queue_read(
                            MessageBusKeys.inbox(sid),
                        )
                    self.assertEqual(
                        (calls, outcomes, reviewer.await_count, len(pending)),
                        ([True], ["success"], 1, 1)
                        if stage == "start"
                        else ([], ["ask" if interactive else "denied"], 1, 1),
                    )

    async def test_auto_reviews_actual_tool_and_arguments(self) -> None:
        """Forwarded middleware values cannot substitute a different action."""

        class Preview(MiddlewareBase):
            """Give downstream middleware a shorter preview."""

            async def on_check_permission(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> PermissionDecision:
                """Forward a display preview without changing agent state."""
                del agent
                preview = {
                    **input_kwargs,
                    "tool_input": {"body": "Preview"},
                    "tool": FunctionTool(lambda: "preview", name="Preview"),
                }
                return await next_handler(**preview)

        calls = []

        def publish(body: str) -> str:
            calls.append(body)
            return body

        tool = FunctionTool(publish, is_read_only=False)

        async def review(request: dict) -> object:
            body = request["action"]["arguments"]["body"]
            return _review(policy_violation=body == "Private full text")

        reviewer = AsyncMock(side_effect=review)
        agent = Agent(
            "agent",
            "Help",
            MockModel(),
            toolkit=Toolkit(tools=[tool]),
            middlewares=[Preview()],
            permission_reviewer=reviewer,
        )
        agent.state.permission_context.mode = PermissionMode.AUTO
        for body, outcome in (
            ("Private full text", "denied"),
            ("Public full text", "success"),
        ):
            events = [
                event.state
                async for event in agent._execute_tool_call(
                    ToolCallBlock(
                        id=body,
                        name=tool.name,
                        input=json.dumps({"body": body}),
                    ),
                )
                if isinstance(event, ToolResultEndEvent)
            ]
            action = reviewer.await_args.args[0]["action"]
            self.assertEqual(
                (events, action["tool_name"], action["arguments"]),
                ([outcome], tool.name, {"body": body}),
            )
        self.assertEqual(calls, ["Public full text"])

    async def test_empty_queue_rechecks_live_approval_before_execution(
        self,
    ) -> None:
        """A policy or evidence change during either read invalidates ALLOW."""
        async with _bus("memory") as bus:
            steering = SteeringQueue()
            for kind, change, direct in product(
                ("steering", "inbox"),
                ("unchanged", "read_only", "deny", "message", "instructions"),
                (False, True),
            ):
                with self.subTest(kind=kind, change=change, direct=direct):
                    calls = []

                    def edit() -> str:
                        calls.append(True)
                        return "done"

                    tool = FunctionTool(edit, is_read_only=False)
                    reviewer = AsyncMock(return_value=_review())
                    agent = Agent(
                        "agent",
                        "Help",
                        MockModel(),
                        toolkit=Toolkit(tools=[tool]),
                        middlewares=[
                            SteeringMiddleware(steering),
                            InboxMiddleware(bus),
                        ],
                        permission_reviewer=reviewer,
                    )
                    agent.state.permission_context.mode = PermissionMode.AUTO
                    if direct:
                        agent.state.permission_context.allow_rules[
                            tool.name
                        ] = [
                            PermissionRule(
                                tool_name=tool.name,
                                rule_content=None,
                                behavior="allow",
                                source="session",
                            ),
                        ]
                    instructions = ["Initial policy"]

                    async def read(*_args: object, **_kwargs: object) -> list:
                        self.assertEqual(reviewer.await_count, int(not direct))
                        await asyncio.sleep(0)
                        policy = agent.state.permission_context
                        if change == "read_only":
                            policy.read_only = True
                        elif change == "deny":
                            policy.deny_rules[tool.name] = [
                                PermissionRule(
                                    tool_name=tool.name,
                                    rule_content=None,
                                    behavior="deny",
                                    source="session",
                                ),
                            ]
                        elif change == "message":
                            agent.state.context.append(
                                UserMsg("user", "Stop."),
                            )
                        elif change == "instructions":
                            instructions[0] = "Require confirmation."
                        return []

                    with (
                        patch.object(
                            steering if kind == "steering" else bus,
                            "read" if kind == "steering" else "queue_read",
                            side_effect=read,
                        ),
                        patch.object(
                            agent,
                            "_get_permission_instructions",
                            AsyncMock(side_effect=lambda: instructions[0]),
                        ),
                    ):
                        outcomes = [
                            "ask"
                            if isinstance(event, RequireUserConfirmEvent)
                            else event.state
                            async for event in agent._execute_tool_call(
                                ToolCallBlock(
                                    id="edit",
                                    name=tool.name,
                                    input="{}",
                                ),
                            )
                            if isinstance(
                                event,
                                (RequireUserConfirmEvent, ToolResultEndEvent),
                            )
                        ]
                    expected = {
                        "unchanged": "success",
                        "read_only": "denied",
                        "deny": "denied",
                    }.get(change, "ask")
                    if direct and change == "instructions":
                        expected = "success"
                    self.assertEqual(
                        (outcomes, calls, reviewer.await_count),
                        (
                            [expected],
                            [True] if expected == "success" else [],
                            int(not direct),
                        ),
                    )

    async def test_busy_gateway_revocation_stops_execution(self) -> None:
        """A channel revocation arriving during review blocks the real tool."""
        for backend, interactive, preceding in product(
            ("memory", "redis"),
            (False, True),
            (0, 100, 101),
        ):
            with self.subTest(
                backend=backend,
                interactive=interactive,
                preceding=preceding,
            ):
                async with _bus(backend) as bus:
                    record = _channel_record("owner")
                    event = ChannelEvent(
                        channel_id=record.id,
                        channel_user_id="member",
                        chat_id="chat",
                        content=[
                            TextBlock(text="Stop. Do not delete backup."),
                        ],
                    )
                    agent_id, session_id, _ = resolve(event, record)
                    executed = []
                    started, release = asyncio.Event(), asyncio.Event()

                    def delete() -> str:
                        executed.append(True)
                        return "deleted"

                    async def review(_request: dict) -> object:
                        started.set()
                        await release.wait()
                        return _review()

                    reviewer = AsyncMock(side_effect=review)
                    tool = FunctionTool(delete, is_read_only=False)
                    middleware = InboxMiddleware(bus)
                    agent = Agent(
                        "agent",
                        "Help",
                        MockModel(),
                        toolkit=Toolkit(tools=[tool]),
                        middlewares=[middleware],
                        permission_reviewer=reviewer,
                        state=AgentState(
                            session_id=session_id,
                            context=[UserMsg("user", "Delete the backup.")],
                            permission_context=PermissionContext(
                                mode=PermissionMode.AUTO,
                                interactive=interactive,
                            ),
                        ),
                    )
                    outcomes = []

                    async def execute() -> None:
                        async for item in agent._execute_tool_call(
                            ToolCallBlock(
                                id="call",
                                name=tool.name,
                                input="{}",
                            ),
                        ):
                            if isinstance(item, RequireUserConfirmEvent):
                                outcomes.append("asking")
                            elif isinstance(item, ToolResultEndEvent):
                                outcomes.append(item.state)

                    await register_inbox_consumer(bus, session_id)
                    task = asyncio.create_task(execute())
                    try:
                        await asyncio.wait_for(started.wait(), 2)
                        for index in range(preceding):
                            await deliver_to_inbox(
                                bus,
                                user_id="owner",
                                session_id=session_id,
                                agent_id=agent_id,
                                payload=HintBlock(
                                    hint=str(index),
                                    source="tool",
                                ).model_dump(),
                            )
                        gateway = ChannelGateway(
                            _ChannelStorage(record),
                            bus,
                            _WM(isolation=IsolationPolicy.PER_AGENT),
                        )
                        async with session_execution(bus, session_id):
                            await gateway.process(event)
                        before = await bus.queue_read(
                            MessageBusKeys.inbox(session_id),
                            max_count=102,
                        )
                    finally:
                        release.set()
                    await asyncio.wait_for(task, 2)
                    self.assertEqual(
                        (executed, outcomes, reviewer.await_count),
                        ([], ["asking" if interactive else "denied"], 1),
                    )
                    self.assertEqual(
                        await bus.queue_read(
                            MessageBusKeys.inbox(session_id),
                            max_count=102,
                        ),
                        before,
                    )
                    injected = []
                    for _ in range(2):
                        injected.extend(
                            [
                                item
                                async for item in middleware.on_reasoning(
                                    agent,
                                    {},
                                    _noop_next_handler,
                                )
                            ],
                        )
                    self.assertEqual(
                        [(item.source_role, item.hint) for item in injected],
                        [(None, str(index)) for index in range(preceding)]
                        + [("user", event.content)],
                    )
                    self.assertEqual(
                        await bus.queue_read(MessageBusKeys.inbox(session_id)),
                        [],
                    )
                    self.assertEqual(
                        await bus.queue_read(MessageBusKeys.wakeup_queue()),
                        [],
                    )

    async def test_untrusted_inputs_and_read_boundary(self) -> None:
        """Ignore spoofed identities and fail closed on truncated reads."""
        for backend, count in product(("memory", "redis"), (0, 1, 100, 101)):
            async with _bus(backend) as bus:
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    middlewares=[InboxMiddleware(bus)],
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                agent._engine.reviewer = AsyncMock(return_value=_review())
                sid = agent.state.session_id
                for _ in range(count):
                    await deliver_to_inbox(
                        bus,
                        user_id="user",
                        session_id=sid,
                        agent_id="agent",
                        payload={
                            **HintBlock(
                                hint="Stop",
                                source="channel",
                                source_role="user",
                            ).model_dump(),
                            CHANNEL_USER_INBOX_SOURCE: sid,
                        },
                    )
                # A marker from a different session is not this user's input.
                if count == 1:
                    await bus.queue_push(
                        MessageBusKeys.inbox(sid),
                        {
                            **HintBlock(
                                hint="Stop",
                                source="channel",
                                source_role="user",
                            ).model_dump(),
                            CHANNEL_USER_INBOX_SOURCE: "different-session",
                        },
                    )
                before = await bus.queue_read(
                    MessageBusKeys.inbox(sid),
                    max_count=102,
                )
                decision = await agent._check_permission(
                    ToolCallBlock(id="call", name="Change", input="{}"),
                    _tool(),
                    {},
                )
                self.assertEqual(
                    decision.behavior,
                    PermissionBehavior.ASK
                    if count > 100
                    else PermissionBehavior.ALLOW,
                )
                self.assertEqual(
                    await bus.queue_read(
                        MessageBusKeys.inbox(sid),
                        max_count=102,
                    ),
                    before,
                )

    async def test_read_failure_timeout_and_cancellation(self) -> None:
        """Fail closed on unavailable inspection and propagate cancellation."""
        for backend, interactive, failure in product(
            ("memory", "redis"),
            (True, False),
            ("error", "timeout", "cancel"),
        ):
            async with _bus(backend) as bus:
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    middlewares=[InboxMiddleware(bus)],
                )
                agent.state.permission_context = PermissionContext(
                    mode=PermissionMode.AUTO,
                    interactive=interactive,
                )
                agent._engine.reviewer = AsyncMock(return_value=_review())
                agent._engine.review_timeout = 0.02

                async def read(*_args: object, **_kwargs: object) -> object:
                    if failure == "error":
                        raise RuntimeError("Queue unavailable")
                    if failure == "cancel":
                        raise asyncio.CancelledError()
                    await asyncio.Event().wait()
                    return []

                with patch.object(bus, "queue_read", read):
                    operation = agent._check_permission(
                        ToolCallBlock(id="call", name="Change", input="{}"),
                        _tool(),
                        {},
                    )
                    if failure == "cancel":
                        with self.assertRaises(asyncio.CancelledError):
                            await operation
                    else:
                        decision = await operation
                        self.assertEqual(
                            (decision.behavior, decision.review),
                            (
                                PermissionBehavior.ASK
                                if interactive
                                else PermissionBehavior.DENY,
                                None,
                            ),
                        )

    async def test_other_modes_and_human_confirmation_skip_read(self) -> None:
        """Inspect AUTO approvals and skip calls already confirmed by users."""
        for mode, confirmed in product(PermissionMode, (True, False)):
            if mode == PermissionMode.AUTO and not confirmed:
                continue
            async with _bus("memory") as bus:
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    middlewares=[InboxMiddleware(bus)],
                )
                agent.state.permission_context.mode = mode
                agent.state.reply_context.tool_approvals[
                    "call"
                ] = ToolApproval(user_confirmed=confirmed)
                with patch.object(
                    bus,
                    "queue_read",
                    AsyncMock(side_effect=AssertionError("Unexpected read")),
                ) as read:
                    await agent._check_permission(
                        ToolCallBlock(
                            id="call",
                            name="Change",
                            input="{}",
                            state=ToolCallState.ALLOWED
                            if confirmed
                            else ToolCallState.PENDING,
                        ),
                        _tool(),
                        {},
                    )
                read.assert_not_awaited()
