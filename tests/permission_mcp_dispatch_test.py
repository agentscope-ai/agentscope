# -*- coding: utf-8 -*-
"""Approval identity across real MCP discovery and dispatch."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from copy import copy
from itertools import product
from typing import Any, AsyncIterator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import mcp.types

from permission_review_test import _review
from realtime_agent_test import ScriptedModel
from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    ToolResultEndEvent,
    UserConfirmResultEvent,
)
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.message import AssistantMsg, ToolCallBlock
from agentscope.middleware import MiddlewareBase
from agentscope.permission import (
    PermissionEngine,
    PermissionDecision,
    PermissionContext,
    PermissionReview,
    PermissionRule,
)
from agentscope.state import AgentState
from agentscope.tool import MCPTool, ToolChunk, Toolkit
from agentscope.workspace._gateway_client import (
    GatewayClient,
    GatewayMCPClient,
)


class MCPDispatchTest(IsolatedAsyncioTestCase):
    """Equivalent wrappers reuse approval only for the same service policy."""

    async def test_engine_pins_identity_before_evaluation(self) -> None:
        """A read-only grant and a model grant refer to the original tool."""
        for read_only, interactive in product((True, False), repeat=2):
            with self.subTest(read_only=read_only, interactive=interactive):
                tool = MCPTool(
                    "editor",
                    mcp.types.Tool(name="render", input_schema={}),
                    session=object(),
                )
                tool.is_read_only = read_only
                engine = PermissionEngine(
                    PermissionContext(mode="auto", interactive=interactive),
                    reviewer=AsyncMock(return_value=_review()),
                )
                original_session = tool._session
                check = tool.check_permissions

                async def wait_for_change(*args: Any) -> PermissionDecision:
                    if tool._session is original_session:
                        await asyncio.sleep(0)
                        tool._session = object()
                    return await check(*args)

                with patch.object(tool, "check_permissions", wait_for_change):
                    decision = await engine.check_permission(tool, {})
                self.assertEqual(
                    (decision.behavior.value, decision.review),
                    ("ask" if interactive else "deny", None),
                )

    async def test_final_dispatch_binds_the_live_mcp_target(self) -> None:
        """Replace the dispatched wrapper while its last check is suspended."""
        for stage, change in product(
            (
                "chat",
                "realtime",
                "chat_recheck",
                "realtime_recheck",
                "guard",
            ),
            ("session", "factory", "definition"),
        ):
            with self.subTest(stage=stage, change=change):

                async def replace_target() -> None:
                    await asyncio.sleep(0)
                    if change == "definition":
                        target._tool.description = "Replacement"
                    elif change == "factory":
                        target._client_gen = AsyncMock()
                    else:
                        target._session = object()

                class Guard(MiddlewareBase):
                    """Hold the final queue inspection or its cleanup."""

                    @asynccontextmanager
                    async def tool_dispatch_guard(
                        self,
                        agent: Agent,
                    ) -> AsyncIterator[Callable[[], bool]]:
                        if stage == "guard":
                            await replace_target()
                        yield lambda: False

                definition = mcp.types.Tool(name="render", input_schema={})
                tool = MCPTool(
                    "editor",
                    definition,
                    client_gen=AsyncMock() if change == "factory" else None,
                    session=None if change == "factory" else object(),
                )
                toolkit = Toolkit(tools=[tool])
                target = copy(tool)
                target._tool = definition.model_copy(deep=True)
                realtime = stage.startswith("realtime")
                waiting_on_guard = stage == "guard"
                state = AgentState(
                    permission_context=PermissionContext(
                        mode="auto",
                        interactive=False,
                    ),
                )
                # Equivalent wrappers share approval. A different definition
                # at final lookup needs a second review before dispatch.
                second_review = stage in ("chat", "realtime")
                if second_review:
                    target._tool.description = "Final target"

                async def review(_request: dict) -> PermissionReview:
                    if toolkit.tool_groups[0].tools[0] is tool:
                        toolkit.tool_groups[0].tools = [target]
                    elif second_review:
                        await replace_target()
                    return _review()

                loads = 0

                async def instructions() -> str:
                    nonlocal loads
                    loads += 1
                    # The third load is the deferred dispatch recheck.
                    if stage.endswith("_recheck") and loads == 3:
                        await replace_target()
                    return "Help"

                call = ToolCallBlock(id="call", name=tool.name, input="{}")
                if realtime:
                    agent = RealtimeAgent(
                        "agent",
                        "Help",
                        ScriptedModel([[]]),
                        toolkit=toolkit,
                        state=state,
                        permission_reviewer=review,
                    )
                else:
                    agent = Agent(
                        "agent",
                        "Help",
                        MockModel(),
                        toolkit=toolkit,
                        state=state,
                        permission_reviewer=review,
                        middlewares=[Guard()] if waiting_on_guard else [],
                    )
                with patch.object(
                    agent,
                    "_get_permission_instructions",
                    instructions,
                ), patch.object(
                    MCPTool,
                    "call",
                    AsyncMock(return_value=ToolChunk(content=[])),
                ) as run:
                    states = await self._outcomes(agent, call)
                self.assertEqual((states, run.await_count), (["denied"], 0))

    async def test_gateway_identity_includes_session_and_service(self) -> None:
        """Rebuilt gateway wrappers share identity only in the same scope."""
        client = GatewayMCPClient(
            name="editor",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://one.invalid"),
        )
        definition = mcp.types.Tool(name="render", input_schema={})
        client._cached_tools = [definition]
        gateway = GatewayClient(AsyncMock(), 8000)
        client.attach(gateway, agent_id="agent", session_id="session")
        tool = await client.get_tool("render")
        same = await client.get_tool("render")
        client.attach(gateway, agent_id="agent", session_id="other")
        other_session = await client.get_tool("render")
        client.attach(
            GatewayClient(AsyncMock(), 8000),
            agent_id="agent",
            session_id="session",
        )
        other_service = await client.get_tool("render")
        identity = tool.approval_identity()
        confirmation = tool.confirmation_identity()
        self.assertEqual(
            [
                (
                    candidate.approval_identity() == identity,
                    candidate.confirmation_identity() == confirmation,
                )
                for candidate in (same, other_session, other_service)
            ],
            [(True, True), (False, False), (False, False)],
        )

    @staticmethod
    async def _outcomes(
        agent: Agent | RealtimeAgent,
        call: ToolCallBlock,
    ) -> list[str]:
        """Collect approval requests and terminal execution states."""
        if isinstance(agent, RealtimeAgent):
            agent._reply_id = agent.state.reply_id
            agent.state.append_context(agent.name, [call])
            await agent._run_tool(agent.state.reply_id, call)
            message = agent.state.get_reply_message(agent.name)
            return [b.state for b in message.get_content_blocks("tool_result")]
        return [
            "ask"
            if isinstance(event, RequireUserConfirmEvent)
            else event.state
            async for event in agent._execute_tool_call(call)
            if isinstance(event, (RequireUserConfirmEvent, ToolResultEndEvent))
        ]

    async def test_mcp_confirmation_survives_only_the_same_target(
        self,
    ) -> None:
        """Restore into a fresh client before consuming human approval."""
        for change in ("same", "service", "definition", "legacy"):
            with self.subTest(change=change):
                definition = mcp.types.Tool(name="render", input_schema={})

                def build(url: str, state: AgentState) -> Agent:
                    client = MCPClient(
                        name="editor",
                        is_stateful=False,
                        mcp_config=HttpMCPConfig(url=url),
                    )
                    client._cached_tools = [definition]
                    return Agent(
                        "agent",
                        "Help",
                        MockModel(),
                        state=state,
                        toolkit=Toolkit(mcps=[client]),
                    )

                agent = build("http://one.invalid", AgentState())
                call = ToolCallBlock(
                    id="one",
                    name="mcp__editor__render",
                    input="{}",
                )
                agent.state.append_context(agent.name, [call])
                with patch.object(
                    MCPClient,
                    "_discover_tools",
                    AsyncMock(return_value=[definition]),
                ), patch.object(
                    MCPTool,
                    "call",
                    AsyncMock(return_value=ToolChunk(content=[])),
                ) as run:
                    (prompt,) = [
                        e async for e in agent._execute_tool_call(call)
                    ]
                    self.assertIsInstance(prompt, RequireUserConfirmEvent)
                    if change == "legacy":
                        agent.state.reply_context.tool_approvals[
                            call.id
                        ].confirmation_target = None
                    restored = AgentState.model_validate_json(
                        agent.state.model_dump_json(),
                    )
                    if change == "definition":
                        definition.description = "Different operation"
                    agent = build(
                        "http://two.invalid"
                        if change == "service"
                        else "http://one.invalid",
                        restored,
                    )
                    result = ConfirmResult(
                        request_id=prompt.id,
                        confirmed=True,
                        tool_call=prompt.tool_calls[0],
                        rules=[
                            PermissionRule(
                                tool_name=call.name,
                                rule_content=None,
                                behavior="allow",
                                source="user",
                            ),
                        ]
                        if change in ("legacy", "service")
                        else prompt.tool_calls[0].suggested_rules,
                    )
                    async for _ in agent._handle_incoming_event(
                        UserConfirmResultEvent(
                            reply_id=agent.state.reply_id,
                            confirm_results=[result],
                        ),
                    ):
                        self.fail("Unexpected confirmation event")
                    (call,) = agent.state.get_unfinished_tool_calls(agent.name)
                    outcomes = await self._outcomes(agent, call)
                    self.assertEqual(
                        (outcomes, run.await_count),
                        (["success"], 1) if change == "same" else (["ask"], 0),
                    )

                    if change != "same":
                        self.assertEqual(
                            agent.state.permission_context.allow_rules,
                            {},
                        )
                        # The first card is still open on a second client.
                        # Restore the second card and replay the late grant.
                        agent.state = AgentState.model_validate_json(
                            agent.state.model_dump_json(),
                        )
                        (pending,) = agent.state.get_awaiting_tool_calls(
                            agent.name,
                        )
                        second_id = agent.state.confirmation_request(
                            agent.name,
                            pending.id,
                        )
                        self.assertNotEqual(second_id, prompt.id)
                        async for _ in agent.reply_stream(
                            UserConfirmResultEvent(
                                reply_id=agent.state.reply_id,
                                confirm_results=[result],
                            ),
                        ):
                            pass
                        self.assertEqual(
                            (
                                agent.state.confirmation_request(
                                    agent.name,
                                    pending.id,
                                ),
                                pending.state,
                                agent.state.permission_context.allow_rules,
                                run.await_count,
                            ),
                            (second_id, "asking", {}, 0),
                        )
                        continue
                    saved = agent.state.model_dump_json()
                    for case, expected in (
                        ("same", "success"),
                        ("service", "ask"),
                        ("definition", "ask"),
                        ("legacy_card", "ask"),
                        ("configured", "success"),
                    ):
                        with self.subTest(saved_grant=case):
                            state = AgentState.model_validate_json(saved)
                            (rule,) = state.permission_context.allow_rules[
                                call.name
                            ]
                            self.assertTrue(rule.confirmation_target)
                            if case in ("legacy_card", "configured"):
                                rule.confirmation_target = None
                            if case == "configured":
                                rule.source = "userSettings"
                            definition.description = (
                                "New definition"
                                if case == "definition"
                                else None
                            )
                            resumed = build(
                                "http://one.invalid"
                                if case in ("same", "definition")
                                else "http://two.invalid",
                                state,
                            )
                            fresh = ToolCallBlock(
                                id="next",
                                name=call.name,
                                input="{}",
                            )
                            resumed.state.append_context(resumed.name, [fresh])
                            run.reset_mock()
                            outcomes = await self._outcomes(resumed, fresh)
                            self.assertEqual(
                                (outcomes, run.await_count),
                                ([expected], int(expected == "success")),
                            )

    async def test_realtime_checks_service_before_saving_grant(self) -> None:
        """A replaced MCP service must receive its own confirmation."""
        for replace_service in (False, True):
            with self.subTest(replace_service=replace_service):
                client = MCPClient(
                    name="editor",
                    is_stateful=False,
                    mcp_config=HttpMCPConfig(url="http://one.invalid"),
                )
                definition = mcp.types.Tool(name="render", input_schema={})
                client._cached_tools = [definition]
                agent = RealtimeAgent(
                    "agent",
                    "Help",
                    ScriptedModel([[]]),
                    toolkit=Toolkit(mcps=[client]),
                )
                call = ToolCallBlock(
                    id="one",
                    name="mcp__editor__render",
                    input="{}",
                )

                prompts = []

                async def answer(confirmed: bool) -> None:
                    while not task.done():
                        for event in agent.drain_events():
                            if isinstance(event, RequireUserConfirmEvent):
                                (asked,) = event.tool_calls
                                if prompts:
                                    (first,) = prompts
                                    self.assertNotEqual(first.id, event.id)
                                    (old,) = first.tool_calls
                                    await agent.send(
                                        UserConfirmResultEvent(
                                            reply_id=event.reply_id,
                                            confirm_results=[
                                                ConfirmResult(
                                                    request_id=first.id,
                                                    confirmed=True,
                                                    tool_call=old,
                                                    rules=old.suggested_rules,
                                                ),
                                            ],
                                        ),
                                    )
                                    self.assertFalse(
                                        agent._confirmations[asked.id].done(),
                                    )
                                prompts.append(event)
                                if replace_service and confirmed:
                                    client.mcp_config.url = (
                                        "http://two.invalid"
                                    )
                                await agent.send(
                                    UserConfirmResultEvent(
                                        reply_id=event.reply_id,
                                        confirm_results=[
                                            ConfirmResult(
                                                request_id=event.id,
                                                confirmed=confirmed,
                                                tool_call=asked,
                                                rules=asked.suggested_rules,
                                            ),
                                        ],
                                    ),
                                )
                                return
                        await asyncio.sleep(0)
                    self.fail("Expected an outstanding confirmation")

                with patch.object(
                    MCPClient,
                    "_discover_tools",
                    AsyncMock(return_value=[definition]),
                ), patch.object(
                    MCPTool,
                    "call",
                    AsyncMock(return_value=ToolChunk(content=[])),
                ) as run:
                    async with agent, asyncio.timeout(3):
                        agent._reply_id = "reply"
                        agent.state.context.append(
                            AssistantMsg("agent", [call], id="reply"),
                        )
                        task = asyncio.create_task(
                            agent._run_tool("reply", call),
                        )
                        try:
                            await answer(True)
                            if replace_service:
                                await answer(False)
                            await task
                        finally:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    rules = agent.state.permission_context.allow_rules
                    tool = await client.get_tool("render")
                    self.assertEqual(
                        (
                            run.await_count,
                            [
                                rule.confirmation_target
                                for rule in rules.get(call.name, [])
                            ],
                        ),
                        (0, [])
                        if replace_service
                        else (1, [tool.confirmation_identity()]),
                    )

    async def test_resolved_mcp_target(self) -> None:
        """Unchanged targets get one review; changed ones need a new grant."""
        for realtime, stateful, change in product(
            (False, True),
            (False, True),
            ("none", "definition", "service", "policy"),
        ):
            with self.subTest(
                realtime=realtime,
                stateful=stateful,
                change=change,
            ):
                client = MCPClient(
                    name="editor",
                    is_stateful=stateful,
                    mcp_config=HttpMCPConfig(url="http://one.invalid"),
                )
                client._is_connected = stateful
                client._client = AsyncMock() if stateful else None
                definition = mcp.types.Tool(name="render", input_schema={})
                client._cached_tools = [definition]
                toolkit = Toolkit(mcps=[client])
                reviews = []

                async def review(request: dict) -> PermissionReview:
                    reviews.append(request["action"]["tool_name"])
                    if len(reviews) > 1:
                        return _review(policy_violation=True)
                    if change == "definition":
                        definition.description = "Changed definition"
                    elif change == "service":
                        client.mcp_config.url = "http://two.invalid"
                    elif change == "policy":
                        client.trust_annotations = False
                    return _review()

                agent = (RealtimeAgent if realtime else Agent)(
                    "agent",
                    "Help",
                    ScriptedModel([[]]) if realtime else MockModel(),
                    toolkit=toolkit,
                    permission_reviewer=review,
                    state=AgentState(
                        permission_context=PermissionContext(mode="auto"),
                    ),
                )
                call = ToolCallBlock(
                    id="call",
                    name="mcp__editor__render",
                    input="{}",
                )
                async with AsyncExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            MCPClient,
                            "_discover_tools",
                            AsyncMock(return_value=[definition]),
                        ),
                    )
                    if realtime:
                        stack.enter_context(
                            patch.object(
                                agent,
                                "_ask_user",
                                AsyncMock(return_value=False),
                            ),
                        )
                    run = stack.enter_context(
                        patch.object(
                            MCPTool,
                            "call",
                            AsyncMock(return_value=ToolChunk(content=[])),
                        ),
                    )
                    states = await self._outcomes(agent, call)
                expected = {
                    "none": ([call.name], ["success"], 1),
                    "definition": (
                        [call.name],
                        ["denied" if realtime else "ask"],
                        0,
                    ),
                    "service": ([call.name, call.name], ["denied"], 0)
                    if stateful
                    else ([call.name], ["denied" if realtime else "ask"], 0),
                    "policy": ([call.name, call.name], ["denied"], 0),
                }
                self.assertEqual(
                    (reviews, states, run.await_count),
                    expected[change],
                )
