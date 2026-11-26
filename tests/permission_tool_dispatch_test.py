# -*- coding: utf-8 -*-
"""The SDK dispatches only the invocation covered by current approval."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import json
from itertools import product
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from permission_review_test import _review
from utils import MockModel

from agentscope.agent import Agent
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    UserConfirmResultEvent,
)
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, UserMsg
from agentscope.middleware import (
    MiddlewareBase,
    SteeringMiddleware,
    SteeringQueue,
    SteeringSource,
)
from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionReview,
    PermissionRule,
)
from agentscope.tool import (
    FunctionTool,
    Toolkit,
    ToolChunk,
    ToolMiddlewareBase,
    ToolResponse,
)


class DispatchTest(IsolatedAsyncioTestCase):
    """Exercise the actual toolkit and the client-side reply projection."""

    async def test_cached_acting_output_rechecks_before_publication(
        self,
    ) -> None:
        """Delayed middleware output must respect current and queued input."""
        for change in ("none", "revoked", "queued"):
            with self.subTest(change=change):
                queue = SteeringQueue()

                class Cache(MiddlewareBase):
                    """Return a cached response after the policy may change."""

                    async def on_acting(
                        self,
                        agent: Agent,
                        input_kwargs: dict,
                        next_handler: Callable,
                    ) -> AsyncGenerator:
                        if change == "revoked":
                            agent.state.context.append(
                                UserMsg("user", "Stop."),
                            )
                        elif change == "queued":
                            queue.submit(
                                UserMsg("user", "Stop."),
                                session_id=agent.state.session_id,
                                reply_id=agent.state.reply_id,
                            )
                        chunk = ToolChunk(
                            content=[TextBlock(text="cached body")],
                        )
                        yield chunk
                        yield ToolResponse().append_chunk(chunk)

                def edit() -> str:
                    self.fail("Cache hit must not invoke the tool")

                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(tools=[FunctionTool(edit)]),
                    middlewares=[SteeringMiddleware(queue), Cache()],
                    permission_reviewer=AsyncMock(return_value=_review()),
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                await queue.start_reply(
                    agent.state.session_id,
                    agent.state.reply_id,
                )
                call = ToolCallBlock(id="edit", name="edit", input="{}")
                agent.state.append_context(agent.name, [call])
                events = [e async for e in agent._execute_tool_call(call)]
                self.assertEqual(
                    (
                        any(
                            isinstance(e, RequireUserConfirmEvent)
                            for e in events
                        ),
                        "cached body" in str(events),
                        "cached body" in str(agent.state.context),
                    ),
                    (change != "none", change == "none", change == "none"),
                )

    async def test_invalid_cached_arguments_close_only_the_tool(self) -> None:
        """A cache hit on invalid rewritten input produces one tool error."""

        class Rewrite(MiddlewareBase):
            """Forward arguments that omit the required value."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                call = input_kwargs["tool_call"].model_copy(
                    update={"input": "{}"},
                )
                async for item in next_handler(tool_call=call):
                    yield item

        class Cache(MiddlewareBase):
            """Serve a cached response without calling the tool."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                yield ToolResponse(content=[TextBlock(text="cached body")])

        def lookup(value: str) -> str:
            self.fail(f"Unexpected invocation with {value!r}")

        agent = Agent(
            "agent",
            "Help",
            MockModel(),
            toolkit=Toolkit(tools=[FunctionTool(lookup, is_read_only=True)]),
            middlewares=[Rewrite(), Cache()],
        )
        call = ToolCallBlock(id="call", name="lookup", input='{"value":"ok"}')
        agent.state.append_context(agent.name, [call])
        events = [e async for e in agent._execute_tool_call(call)]
        self.assertEqual(
            [type(e).__name__ for e in events],
            [
                "ToolResultStartEvent",
                "ToolResultTextDeltaEvent",
                "ToolResultEndEvent",
            ],
        )
        self.assertEqual((events[-1].state, call.state), ("error", "finished"))
        self.assertIn("validation failed", str(events))
        self.assertNotIn("cached body", str(events) + str(agent.state.context))

    async def test_cache_queries_confirmed_edit_after_rewrite(self) -> None:
        """The cache key, result and confirmation use the same arguments."""

        class Rewrite(MiddlewareBase):
            """Append punctuation before the cache is queried."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                call = input_kwargs["tool_call"].model_copy()
                value = json.loads(call.input)["value"]
                call.input = json.dumps({"value": f"{value}."})
                async for item in next_handler(tool_call=call):
                    yield item

        class Cache(MiddlewareBase):
            """Serve the forwarded value when the entry exists."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                value = json.loads(input_kwargs["tool_call"].input)["value"]
                queried.append(value)
                if value == "draft." or hit:
                    yield ToolResponse(content=[TextBlock(text=value)])
                else:
                    async for item in next_handler():
                        yield item

        for hit in (True, False):
            with self.subTest(hit=hit):
                queried = []
                executed = []

                def lookup(value: str) -> str:
                    executed.append(value)
                    return value

                reviewer = AsyncMock(side_effect=[_review(), ValueError()])
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(tools=[FunctionTool(lookup)]),
                    middlewares=[Rewrite(), Cache()],
                    permission_reviewer=reviewer,
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                call = ToolCallBlock(
                    id="lookup",
                    name="lookup",
                    input='{"value":"draft"}',
                )
                agent.state.append_context(agent.name, [call])
                (card,) = [e async for e in agent._execute_tool_call(call)]
                self.assertIsInstance(card, RequireUserConfirmEvent)
                self.assertEqual(
                    json.loads(card.tool_calls[0].input),
                    {"value": "draft."},
                )
                agent.state = type(agent.state).model_validate_json(
                    agent.state.model_dump_json(),
                )
                edited = card.tool_calls[0].model_copy(
                    update={"input": '{"value":"edited"}'},
                )
                receipt = UserConfirmResultEvent(
                    reply_id=agent.state.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            confirmed=True,
                            request_id=card.id,
                            tool_call=edited,
                        ),
                    ],
                )
                async for _ in agent._handle_incoming_event(receipt):
                    self.fail("Valid receipt should resume the call")
                (call,) = agent.state.get_unfinished_tool_calls(agent.name)
                events = [e async for e in agent._execute_tool_call(call)]
                message = agent.state.get_reply_message(agent.name)
                (result,) = message.get_content_blocks("tool_result")
                self.assertEqual(
                    (
                        queried,
                        executed,
                        json.loads(call.input),
                        [item.text for item in result.output],
                        [
                            e.state
                            for e in events
                            if isinstance(e, ToolResultEndEvent)
                        ],
                        sum(
                            isinstance(e, RequireUserConfirmEvent)
                            for e in events
                        ),
                    ),
                    (
                        ["draft.", "edited"],
                        [] if hit else ["edited"],
                        {"value": "edited"},
                        ["edited"],
                        ["success"],
                        0,
                    ),
                )

    async def test_sdk_dispatch_rechecks_without_queue_middleware(
        self,
    ) -> None:
        """Policy and evidence changes during tool lookup invalidate AUTO."""
        for change, expected in (
            ("none", ["success"]),
            ("read_only", ["denied"]),
            ("revoke", ["ask"]),
        ):
            with self.subTest(change=change):
                calls = []

                def edit() -> str:
                    calls.append(True)
                    return "done"

                tool = FunctionTool(edit, is_read_only=False)
                toolkit = Toolkit(tools=[tool])
                reviewer = AsyncMock(return_value=_review())
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=toolkit,
                    permission_reviewer=reviewer,
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                lookup = toolkit._get_available_tools

                async def final_lookup(groups: list[str]) -> dict:
                    if reviewer.await_count:
                        if change == "read_only":
                            agent.state.permission_context.read_only = True
                        elif change == "revoke":
                            agent.state.context.append(UserMsg("user", "Stop"))
                    return await lookup(groups)

                with patch.object(
                    toolkit,
                    "_get_available_tools",
                    final_lookup,
                ):
                    events = [
                        e
                        async for e in agent._execute_tool_call(
                            ToolCallBlock(id="edit", name="edit", input="{}"),
                        )
                    ]
                outcomes = [
                    "ask"
                    if isinstance(e, RequireUserConfirmEvent)
                    else e.state
                    for e in events
                    if isinstance(
                        e,
                        (RequireUserConfirmEvent, ToolResultEndEvent),
                    )
                ]
                self.assertEqual(
                    (outcomes, calls, reviewer.await_count),
                    (expected, [True] if change == "none" else [], 1),
                )
                if change == "revoke":
                    self.assertEqual(
                        [type(e) for e in events],
                        [RequireUserConfirmEvent],
                    )

    async def test_ask_and_bypass_check_final_tool_arguments(self) -> None:
        """Confirm the rewritten input or deny it before the body runs."""

        class Rewrite(ToolMiddlewareBase):
            """Forward a different input to the tool."""

            async def on_tool_call(
                self,
                tool: FunctionTool,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                async for item in next_handler(value="changed"):
                    yield item

        class Edit(FunctionTool):
            """Match deny rules against the forwarded value."""

            async def match_rule(
                self,
                rule_content: str | None,
                tool_input: dict,
            ) -> bool:
                return tool_input["value"] == rule_content

        class Grant(MiddlewareBase):
            """Application grants may resolve soft requests."""

            async def on_check_permission(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> PermissionDecision:
                return PermissionDecision(PermissionBehavior.ALLOW, "Allowed")

        for mode, grant in product(
            (PermissionMode.ASK, PermissionMode.BYPASS),
            (False, True),
        ):
            with self.subTest(mode=mode, grant=grant):
                calls = []

                def edit(value: str) -> str:
                    calls.append(value)
                    return value

                tool = Edit(edit, middlewares=[Rewrite()])
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(tools=[tool]),
                    middlewares=[Grant()] if grant else [],
                )
                agent.state.permission_context.mode = mode
                call = ToolCallBlock(
                    id="one",
                    name=tool.name,
                    input='{"value":"draft"}',
                )
                agent.state.append_context(agent.name, [call])
                if mode == PermissionMode.BYPASS:
                    agent._engine.add_rule(
                        PermissionRule(
                            tool_name=tool.name,
                            rule_content="changed",
                            behavior="deny",
                            source="user",
                        ),
                    )
                elif not grant:
                    for value in ("draft", "changed"):
                        events = [
                            e async for e in agent._execute_tool_call(call)
                        ]
                        self.assertEqual(
                            ([type(e) for e in events], calls),
                            ([RequireUserConfirmEvent], []),
                        )
                        prompt = events[0].tool_calls[0]
                        self.assertEqual(
                            json.loads(prompt.input),
                            {"value": value},
                        )
                        result = ConfirmResult(
                            request_id=events[0].id,
                            confirmed=True,
                            tool_call=prompt,
                        )
                        async for _ in agent._handle_incoming_event(
                            UserConfirmResultEvent(
                                reply_id=agent.state.reply_id,
                                confirm_results=[result],
                            ),
                        ):
                            self.fail("Unexpected confirmation event")
                outcomes = [
                    e.state
                    async for e in agent._execute_tool_call(call)
                    if isinstance(e, ToolResultEndEvent)
                ]
                self.assertEqual(
                    (outcomes, calls),
                    (["success"], ["changed"])
                    if mode == PermissionMode.ASK
                    else (["denied"], []),
                )

    async def test_changed_acting_target_and_confirmation_replay(self) -> None:
        """Persist and confirm exactly what the acting middleware forwards."""

        class Rewrite(MiddlewareBase):
            """Forward a different tool and different arguments."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                call = input_kwargs["tool_call"].model_copy()
                value = json.loads(call.input)["value"]
                call.name = target_name
                call.input = json.dumps({"value": f"{value}."})
                async for item in next_handler(tool_call=call):
                    yield item

        class ToolRewrite(ToolMiddlewareBase):
            """Append a suffix at the innermost middleware boundary."""

            async def on_tool_call(
                self,
                tool: FunctionTool,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                arguments = {
                    **input_kwargs,
                    "value": input_kwargs["value"] + "!",
                }
                async for item in next_handler(**arguments):
                    yield item

        for target_name, outcome, tool_middleware in product(
            ("edit", "adjust"),
            ("success", "denied", "ask", "edit", "read_only"),
            (False, True),
        ):
            with self.subTest(
                target=target_name,
                outcome=outcome,
                middleware=tool_middleware,
            ):
                forwarded = "draft.!" if tool_middleware else "draft."
                calls = []

                def change(value: str) -> str:
                    calls.append(value)
                    return value

                original = FunctionTool(
                    change,
                    name="edit",
                    is_read_only=False,
                    middlewares=[ToolRewrite()] if tool_middleware else [],
                )
                target = FunctionTool(
                    change,
                    name="adjust",
                    is_read_only=False,
                    middlewares=[ToolRewrite()] if tool_middleware else [],
                )
                reviews = []

                async def review(request: dict) -> PermissionReview:
                    action = request["action"]
                    reviews.append((action["tool_name"], action["arguments"]))
                    if action["arguments"] == {"value": forwarded}:
                        if outcome in ("ask", "edit", "read_only"):
                            raise ValueError("Unavailable")
                        return _review(policy_violation=outcome == "denied")
                    return _review()

                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(tools=[original, target]),
                    middlewares=[Rewrite()],
                    permission_reviewer=review,
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                call = ToolCallBlock(
                    id="one",
                    name="edit",
                    input='{"value":"draft"}',
                )
                agent.state.append_context(agent.name, [call])
                replay = AssistantMsg(
                    agent.name,
                    [call.model_copy(deep=True)],
                    id=agent.state.reply_id,
                )
                final_value = "edited" if outcome == "edit" else forwarded
                denied = outcome in ("denied", "read_only")
                events = [e async for e in agent._execute_tool_call(call)]
                for event in events:
                    replay.append_event(event)
                if outcome in ("ask", "edit", "read_only"):
                    self.assertEqual(
                        (
                            [type(e) for e in events],
                            calls,
                            replay.get_content_blocks("tool_result"),
                        ),
                        ([RequireUserConfirmEvent], [], []),
                    )
                    actual = replay.content[0]
                    assert isinstance(actual, ToolCallBlock)
                    self.assertEqual(
                        (actual.name, json.loads(actual.input), actual.state),
                        (target_name, {"value": forwarded}, "asking"),
                    )
                    # Resume through the checkpoint and confirmation paths.
                    agent.state = type(agent.state).model_validate_json(
                        agent.state.model_dump_json(),
                    )
                    call = agent.state.get_unfinished_tool_calls(agent.name)[0]
                    if outcome == "edit":
                        actual = actual.model_copy(
                            update={
                                "input": json.dumps({"value": final_value}),
                            },
                        )
                    if outcome == "read_only":
                        agent.state.permission_context.read_only = True
                    confirmation = UserConfirmResultEvent(
                        reply_id=agent.state.reply_id,
                        confirm_results=[
                            ConfirmResult(
                                request_id=events[0].id,
                                confirmed=True,
                                tool_call=actual,
                            ),
                        ],
                    )
                    replay.append_event(confirmation)
                    async for _ in agent._handle_incoming_event(confirmation):
                        self.fail(
                            "Accepting confirmation should only update state",
                        )
                    with patch.object(
                        agent._engine,
                        "dispatch_basis",
                        wraps=agent._engine.dispatch_basis,
                    ) as snapshot:
                        async for event in agent._execute_tool_call(call):
                            replay.append_event(event)
                            events.append(event)
                    snapshot.assert_not_called()
                projected = replay.content[0]
                assert isinstance(projected, ToolCallBlock)
                self.assertEqual(
                    (projected.name, json.loads(projected.input)),
                    (target_name, {"value": final_value}),
                )
                results = [
                    b for b in replay.content if b.type == "tool_result"
                ]
                self.assertEqual(
                    (
                        reviews,
                        calls,
                        [(r.id, r.name, r.state) for r in results],
                        sum(
                            isinstance(e, ToolResultStartEvent) for e in events
                        ),
                        sum(isinstance(e, ToolResultEndEvent) for e in events),
                    ),
                    (
                        [
                            ("edit", {"value": "draft"}),
                            (target_name, {"value": forwarded}),
                        ],
                        [] if denied else [final_value],
                        [
                            (
                                "one",
                                target_name,
                                "denied" if denied else "success",
                            ),
                        ],
                        1,
                        1,
                    ),
                )

    async def test_legacy_source_requires_confirmation_or_denial(self) -> None:
        """A one-shot read cannot authorize concurrent dispatch."""

        class LegacyQueue(SteeringQueue):
            """Simulate a source implementing only the original interface."""

            dispatch_inputs = SteeringSource.dispatch_inputs

        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                calls = []

                def edit() -> str:
                    calls.append(True)
                    return "ok"

                source = LegacyQueue()
                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(
                        tools=[FunctionTool(edit, is_read_only=False)],
                    ),
                    middlewares=[SteeringMiddleware(source)],
                    permission_reviewer=AsyncMock(return_value=_review()),
                )
                policy = agent.state.permission_context
                policy.mode, policy.interactive = (
                    PermissionMode.AUTO,
                    interactive,
                )
                events = [
                    e
                    async for e in agent._execute_tool_call(
                        ToolCallBlock(id="edit", name="edit", input="{}"),
                    )
                ]
                self.assertEqual(
                    (
                        calls,
                        [
                            "ask"
                            if isinstance(e, RequireUserConfirmEvent)
                            else e.state
                            for e in events
                            if isinstance(
                                e,
                                (RequireUserConfirmEvent, ToolResultEndEvent),
                            )
                        ],
                    ),
                    ([], ["ask" if interactive else "denied"]),
                )

    async def test_offload_timeout_starts_after_final_approval(self) -> None:
        """A slow dispatch check stays foreground and can still park."""
        entered, release = asyncio.Event(), asyncio.Event()
        manager = AsyncMock()
        calls = []

        def edit() -> str:
            calls.append(True)
            return "done"

        agent = Agent(
            "agent",
            "Help",
            MockModel(),
            toolkit=Toolkit(tools=[FunctionTool(edit, is_read_only=False)]),
            middlewares=[
                ToolOffloadMiddleware(
                    manager,
                    AsyncMock(),
                    "user",
                    "agent",
                    timeout_secs=0.01,
                ),
            ],
            permission_reviewer=AsyncMock(return_value=_review()),
        )
        agent.state.permission_context.mode = PermissionMode.AUTO
        loads = 0

        async def instructions() -> str:
            nonlocal loads
            loads += 1
            if loads == 3:
                entered.set()
                await release.wait()
                return "Require user confirmation"
            return "Help"

        stream = agent._execute_tool_call(
            ToolCallBlock(id="edit", name="edit", input="{}"),
        )
        with patch.object(agent, "_get_permission_instructions", instructions):
            first = asyncio.create_task(anext(stream))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                done, _ = await asyncio.wait((first,), timeout=0.04)
                self.assertEqual(done, set())
            finally:
                release.set()
            self.assertIsInstance(await first, RequireUserConfirmEvent)
            self.assertEqual([item async for item in stream], [])
        self.assertEqual((calls, manager.register_task.await_count), ([], 0))

    async def test_close_joins_only_foreground_drain(self) -> None:
        """Only an offloaded tool survives closing the result stream."""
        for offload in (False, True):
            with self.subTest(offload=offload):
                manager = AsyncMock()
                manager.register_task.return_value = "background"
                bus = await self.enterAsyncContext(InMemoryMessageBus())
                finish, stopped = asyncio.Event(), asyncio.Event()
                calls = []

                async def edit() -> str:
                    try:
                        await finish.wait()
                        calls.append("done")
                        return "done"
                    finally:
                        stopped.set()

                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    toolkit=Toolkit(tools=[FunctionTool(edit)]),
                    middlewares=[
                        ToolOffloadMiddleware(
                            manager,
                            bus,
                            "user",
                            "agent",
                            timeout_secs=0.01 if offload else 60,
                        ),
                    ],
                    permission_reviewer=AsyncMock(return_value=_review()),
                )
                agent.state.permission_context.mode = PermissionMode.AUTO
                stream = agent._execute_tool_call(
                    ToolCallBlock(id="edit", name="edit", input="{}"),
                )
                try:
                    self.assertIsInstance(
                        await anext(stream),
                        ToolResultStartEvent,
                    )
                    if offload:
                        await asyncio.wait_for(anext(stream), 2)
                    # Let the middleware enter its foreground wait.
                    await asyncio.sleep(0)
                    await asyncio.wait_for(stream.aclose(), 2)
                    self.assertEqual(
                        (
                            stopped.is_set(),
                            calls,
                            manager.register_task.await_count,
                        ),
                        (not offload, [], int(offload)),
                    )
                finally:
                    finish.set()
                    await asyncio.wait_for(stopped.wait(), 2)
                self.assertEqual(calls, ["done"] if offload else [])

    async def test_start_arrives_while_tool_is_running(self) -> None:
        """Expose start after commitment and before slow tools finish."""
        entered, finish = asyncio.Event(), asyncio.Event()

        async def edit() -> str:
            entered.set()
            await finish.wait()
            return "done"

        agent = Agent(
            "agent",
            "Help",
            MockModel(),
            toolkit=Toolkit(tools=[FunctionTool(edit, is_read_only=False)]),
            permission_reviewer=AsyncMock(return_value=_review()),
        )
        agent.state.permission_context.mode = PermissionMode.AUTO
        stream = agent._execute_tool_call(
            ToolCallBlock(id="edit", name="edit", input="{}"),
        )
        try:
            event = await asyncio.wait_for(anext(stream), 2)
            self.assertEqual(
                (type(event), entered.is_set()),
                (ToolResultStartEvent, True),
            )
        finally:
            finish.set()
        self.assertEqual(
            [
                e.state
                async for e in stream
                if isinstance(e, ToolResultEndEvent)
            ],
            ["success"],
        )
