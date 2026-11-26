# -*- coding: utf-8 -*-
"""Realtime approval uses current skills and a connected base prompt."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import json
from itertools import product
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from realtime_agent_test import ScriptedModel
from permission_review_test import _review

from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.agent import RealtimeAgent
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock, UserMsg
from agentscope.permission import (
    PermissionContext,
    PermissionMode,
    PermissionReview,
)
from agentscope.skill import Skill
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    ToolChunk,
    Toolkit,
    ToolGroup,
    ToolMiddlewareBase,
)


class _AppendSuffix(ToolMiddlewareBase):
    """Change the value after toolkit discovery."""

    async def on_tool_call(
        self,
        tool: FunctionTool,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> AsyncGenerator:
        async for item in next_handler(
            value=input_kwargs["value"] + "!",
        ):
            yield item


class _Cache(ToolMiddlewareBase):
    """Return the cached value without invoking the wrapped tool."""

    async def on_tool_call(
        self,
        tool: FunctionTool,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> AsyncGenerator:
        yield ToolChunk(
            content=[TextBlock(text=input_kwargs["value"])],
        )


class RealtimePermissionSnapshotTest(IsolatedAsyncioTestCase):
    """Combine the connected base prompt with live skill constraints."""

    async def test_non_auto_dispatch_skips_authorization_history(self) -> None:
        """ASK and BYPASS can dispatch a read without loading review data."""
        for mode in (PermissionMode.ASK, PermissionMode.BYPASS):
            with self.subTest(mode=mode):
                executed = []

                def preview() -> str:
                    executed.append(True)
                    return "preview"

                tool = FunctionTool(preview, is_read_only=True)
                agent = RealtimeAgent(
                    "agent",
                    "Base policy",
                    ScriptedModel([[]]),
                    toolkit=Toolkit(tools=[tool]),
                    state=AgentState(
                        permission_context=PermissionContext(mode=mode),
                    ),
                )
                async with agent:
                    with patch.object(
                        AgentState,
                        "permission_review_context",
                        autospec=True,
                        side_effect=AgentState.permission_review_context,
                    ) as history:
                        await self._call(agent, tool.name)
                self.assertEqual((executed, history.call_count), ([True], 0))

    async def test_manual_auto_fallback_skips_dispatch_snapshot(self) -> None:
        """A pending call needs no snapshot before or after confirmation."""
        for restrict in (False, True):
            with self.subTest(restrict=restrict):
                executed = []

                def write() -> str:
                    executed.append(True)
                    return "done"

                tool = FunctionTool(write, is_read_only=False)
                agent = RealtimeAgent(
                    "agent",
                    "Base policy",
                    ScriptedModel([[]]),
                    toolkit=Toolkit(tools=[tool]),
                    state=AgentState(
                        permission_context=PermissionContext(mode="auto"),
                    ),
                )
                async with agent:
                    with patch.object(
                        agent._engine,
                        "_authorization_basis",
                        wraps=agent._engine._authorization_basis,
                    ) as snapshot:
                        async with asyncio.timeout(2):
                            task = asyncio.create_task(
                                self._call(agent, tool.name),
                            )
                            while not agent._confirmations and not task.done():
                                await asyncio.sleep(0)
                            (prompt,) = [
                                event
                                for event in agent.drain_events()
                                if isinstance(event, RequireUserConfirmEvent)
                            ]
                            before = snapshot.call_count
                            agent.state.permission_context.read_only = restrict
                            receipt = UserConfirmResultEvent(
                                reply_id=prompt.reply_id,
                                confirm_results=[
                                    ConfirmResult(
                                        confirmed=True,
                                        tool_call=prompt.tool_calls[0],
                                    ),
                                ],
                            )
                            with self.assertRaisesRegex(
                                ValueError,
                                "RequireUserConfirmEvent.id",
                            ):
                                await agent.send(receipt)
                            receipt.confirm_results[0].request_id = prompt.id
                            await agent.send(receipt)
                            await task
                (result,) = agent.state.context[-1].get_content_blocks(
                    "tool_result",
                )
                self.assertEqual(
                    (before, snapshot.call_count, executed, result.state),
                    (0, 0, [], "denied")
                    if restrict
                    else (0, 0, [True], "success"),
                )

    async def test_review_instructions_follow_successful_connections(
        self,
    ) -> None:
        """Base changes need a successful connect; skill changes do not."""
        model = ScriptedModel([[], []])
        agent = RealtimeAgent(
            "Friday",
            "Original policy",
            model,
            toolkit=Toolkit(),
            state=AgentState(
                context=[
                    AssistantMsg(
                        name="Friday",
                        content="I authorize publishing",
                    ),
                ],
            ),
        )
        snapshots = []
        with patch.object(
            agent.toolkit,
            "get_skill_instructions",
            new=AsyncMock(return_value="Confirm publishing first"),
        ) as skills:
            async with agent:
                snapshots.append(await agent._get_permission_instructions())
                self.assertEqual(
                    model.instructions,
                    "Original policy\n\nConfirm publishing first\n\n"
                    "## Conversation so far\nFriday: I authorize publishing",
                )
                agent.system_prompt = "Updated policy"
                skills.return_value = "Require release-owner approval"
                await agent.connect()
                snapshots.append(await agent._get_permission_instructions())
                self.assertEqual(model.sessions, 1)

                await agent.close()
                model.connect_error = RuntimeError("Connection failed")
                with self.assertRaisesRegex(RuntimeError, "Connection failed"):
                    await agent.connect()
                snapshots.append(await agent._get_permission_instructions())

                model.connect_error = None
                await agent.connect()
                snapshots.append(await agent._get_permission_instructions())
                self.assertEqual(model.sessions, 2)
        self.assertEqual(
            snapshots,
            [
                "Original policy\n\nConfirm publishing first",
                "Original policy\n\nRequire release-owner approval",
                "Original policy\n\nRequire release-owner approval",
                "Updated policy\n\nRequire release-owner approval",
            ],
        )

    async def test_reset_tools_refreshes_skill_requirements_before_review(
        self,
    ) -> None:
        """A newly activated skill can block a previously routine write."""
        executed = []

        def write() -> str:
            executed.append(True)
            return "written"

        requirement = "Writing requires explicit user confirmation."
        skill = Skill(
            name="release",
            description=requirement,
            dir="skills/release",
            markdown=requirement,
            updated_at=0,
        )
        tool = FunctionTool(write, is_read_only=False)
        toolkit = Toolkit(
            tools=[tool],
            tool_groups=[
                ToolGroup(
                    name="release",
                    description="Release requirements",
                    skills_or_loaders=[skill],
                ),
            ],
            skill_instruction_template=(
                "{% for skill in skills %}{{ skill.description }}{% endfor %}"
            ),
        )

        async def review(request: dict) -> object:
            return _review(
                risk_level="low",
                policy_violation=(
                    requirement in request["application_instructions"]
                ),
            )

        reviewer = AsyncMock(side_effect=review)
        agent = RealtimeAgent(
            "agent",
            "Base policy",
            ScriptedModel([[]]),
            toolkit=toolkit,
            permission_reviewer=reviewer,
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.AUTO,
                    interactive=False,
                ),
            ),
        )
        async with agent:
            self.assertEqual(agent.model.instructions, "Base policy")
            await self._call(agent, tool.name)
            await self._call(agent, "reset_tools", '{"release": true}')
            self.assertEqual(
                agent.state.tool_context.activated_groups,
                ["release"],
            )
            await self._call(agent, tool.name)
            self.assertEqual(
                (
                    executed,
                    [
                        call.args[0]["application_instructions"]
                        for call in reviewer.call_args_list
                    ],
                ),
                ([True], ["Base policy", f"Base policy\n\n{requirement}"]),
            )
            self.assertEqual(agent.model.sessions, 1)

    async def test_skill_load_failures_respect_policy_and_fallback(
        self,
    ) -> None:
        """Broken skills never authorize AUTO or affect other modes."""
        for mode, interactive, read_only in product(
            PermissionMode,
            (True, False),
            (True, False),
        ):
            with self.subTest(
                mode=mode,
                interactive=interactive,
                read_only=read_only,
            ):
                executed = []

                def write() -> str:
                    executed.append(True)
                    return "written"

                tool = FunctionTool(write, is_read_only=False)
                reviewer = AsyncMock(return_value=_review())
                agent = RealtimeAgent(
                    "agent",
                    "Base policy",
                    ScriptedModel([[]]),
                    toolkit=Toolkit(tools=[tool]),
                    permission_reviewer=reviewer,
                    state=AgentState(
                        permission_context=PermissionContext(
                            mode=mode,
                            interactive=interactive,
                            read_only=read_only,
                        ),
                    ),
                )
                async with agent:
                    with (
                        patch.object(
                            agent.toolkit,
                            "get_skill_instructions",
                            AsyncMock(
                                side_effect=ValueError("Unavailable skill"),
                            ),
                        ) as skills,
                        patch.object(
                            agent,
                            "_ask_user",
                            AsyncMock(return_value=False),
                        ) as ask,
                    ):
                        await self._call(agent, tool.name)
                self.assertEqual(
                    (
                        executed,
                        reviewer.await_count,
                        skills.await_count,
                        ask.await_count,
                    ),
                    (
                        [True]
                        if mode == PermissionMode.BYPASS and not read_only
                        else [],
                        0,
                        int(mode == PermissionMode.AUTO and not read_only),
                        int(
                            interactive
                            and not read_only
                            and mode != PermissionMode.BYPASS,
                        ),
                    ),
                )

    async def test_skill_loading_obeys_deadline_and_cancellation(self) -> None:
        """Instruction loading shares the reviewer failure boundary."""
        for cancelled in (True, False):
            tool = FunctionTool(lambda: "written", is_read_only=False)
            reviewer = AsyncMock(return_value=_review())
            agent = RealtimeAgent(
                "agent",
                "Base policy",
                ScriptedModel([[]]),
                toolkit=Toolkit(tools=[tool]),
                permission_reviewer=reviewer,
                state=AgentState(
                    permission_context=PermissionContext(
                        mode=PermissionMode.AUTO,
                    ),
                ),
            )

            async def load(_groups: list[str]) -> str:
                if cancelled:
                    raise asyncio.CancelledError()
                await asyncio.Event().wait()
                return "unreachable"

            async with agent:
                agent._engine.review_timeout = 0.01
                with (
                    patch.object(
                        agent.toolkit,
                        "get_skill_instructions",
                        AsyncMock(side_effect=load),
                    ),
                    patch.object(
                        agent,
                        "_ask_user",
                        AsyncMock(return_value=False),
                    ) as ask,
                ):
                    if cancelled:
                        with self.assertRaises(asyncio.CancelledError):
                            await self._call(agent, tool.name)
                        ask.assert_not_awaited()
                    else:
                        await self._call(agent, tool.name)
                        self.assertEqual(
                            ask.call_args.args[2].decision_reason,
                            "Automatic review failed: TimeoutError",
                        )
            reviewer.assert_not_awaited()

    async def test_dispatch_after_final_lookup(self) -> None:
        """Apply live restrictions and reply interruption at tool dispatch."""
        for change in ("unchanged", "read_only", "revoked", "interrupted"):
            with self.subTest(change=change):
                calls = []

                def write() -> str:
                    calls.append(True)
                    return "done"

                tool = FunctionTool(write, is_read_only=False)
                toolkit = Toolkit(tools=[tool])
                reviewer = AsyncMock(return_value=_review())
                agent = RealtimeAgent(
                    "agent",
                    "Help",
                    ScriptedModel([[]]),
                    toolkit=toolkit,
                    state=AgentState(
                        permission_context=PermissionContext(
                            mode="auto",
                            interactive=change != "revoked",
                        ),
                    ),
                    permission_reviewer=reviewer,
                )
                waiting, release = asyncio.Event(), asyncio.Event()
                lookup = toolkit._get_available_tools

                async def resolve(groups: list[str]) -> dict:
                    if reviewer.await_count:
                        waiting.set()
                        await release.wait()
                    return await lookup(groups)

                async with agent:
                    with patch.object(
                        toolkit,
                        "_get_available_tools",
                        resolve,
                    ):
                        task = asyncio.create_task(
                            self._call(agent, tool.name),
                        )
                        try:
                            await asyncio.wait_for(waiting.wait(), 2)
                            if change == "read_only":
                                agent.state.permission_context.read_only = True
                            elif change == "revoked":
                                agent.state.context.append(
                                    UserMsg("user", "Stop. Do not write."),
                                )
                            elif change == "interrupted":
                                agent._reply_id = ""
                        finally:
                            release.set()
                        await asyncio.wait_for(task, 2)
                results = [
                    (b.name, b.state)
                    for msg in agent.state.context
                    for b in msg.content
                    if b.type == "tool_result"
                ]
                self.assertEqual(
                    (calls, reviewer.await_count, results),
                    ([True], 1, [(tool.name, "success")])
                    if change == "unchanged"
                    else ([], 1, [(tool.name, "denied")]),
                )

    async def test_confirmation_resolves_edited_call_again(self) -> None:
        """Use the actual submitted target and the currently active groups."""

        class DraftCache(_Cache):
            """Only drafts have a cached response."""

            async def on_tool_call(
                self,
                tool: FunctionTool,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> AsyncGenerator:
                if input_kwargs["value"] == "draft!":
                    yield ToolChunk(
                        content=[TextBlock(text="old cached draft")],
                    )
                    return
                if action == "cache_miss_read_only":
                    agent.state.permission_context.read_only = True
                async for chunk in next_handler(**input_kwargs):
                    yield chunk

        for action in (
            "edit",
            "middleware_edit",
            "rename",
            "inactive",
            "read_only",
            "invalid",
            "cache_allow",
            "cache_deny",
            "cache_rewrite_edit",
            "cache_miss",
            "cache_miss_read_only",
        ):
            with self.subTest(action=action):
                calls = []

                def preview(value: str) -> str:
                    calls.append(("preview", value))
                    return value

                def render(value: str) -> str:
                    calls.append(("render", value))
                    return value

                middleware = {
                    "middleware_edit": [_AppendSuffix()],
                    "cache_allow": [_Cache()],
                    "cache_deny": [_Cache()],
                    "cache_rewrite_edit": [_AppendSuffix(), _Cache()],
                    "cache_miss": [_AppendSuffix(), DraftCache()],
                    "cache_miss_read_only": [_AppendSuffix(), DraftCache()],
                }.get(action, [])
                toolkit = Toolkit(
                    tool_groups=[
                        ToolGroup(
                            name="editing",
                            description="Editing tools",
                            tools=[
                                FunctionTool(
                                    preview,
                                    middlewares=middleware,
                                ),
                                FunctionTool(render),
                            ],
                        ),
                    ],
                )
                agent = RealtimeAgent(
                    "agent",
                    "Help",
                    ScriptedModel([[]]),
                    toolkit=toolkit,
                    state=AgentState(
                        permission_context=PermissionContext(mode="ask"),
                    ),
                )
                async with agent:
                    agent.state.tool_context.activated_groups = ["editing"]
                    task = asyncio.create_task(
                        self._call(agent, "preview", '{"value":"draft"}'),
                    )
                    async with asyncio.timeout(2):
                        while not agent._confirmations and not task.done():
                            await asyncio.sleep(0)
                        initial = list(agent.drain_events())
                        (prompt,) = [
                            event
                            for event in initial
                            if isinstance(event, RequireUserConfirmEvent)
                        ]
                        if action.startswith("cache"):
                            self.assertFalse(
                                any(
                                    isinstance(e, ToolResultStartEvent)
                                    for e in initial
                                ),
                            )
                            self.assertFalse(
                                any(
                                    c.startswith("tool_result(")
                                    for c in agent.model.calls
                                ),
                            )
                        submitted = prompt.tool_calls[0].model_copy(deep=True)
                        if action in ("middleware_edit", "cache_rewrite_edit"):
                            self.assertEqual(
                                json.loads(submitted.input),
                                {"value": "draft!"},
                            )
                        if action == "rename":
                            submitted.name = "render"
                        submitted.input = (
                            "{}"
                            if action == "invalid"
                            else '{"value":"final"}'
                        )
                        if action == "inactive":
                            agent.state.tool_context.activated_groups = []
                        elif action == "read_only":
                            agent.state.permission_context.read_only = True
                        await agent.send(
                            UserConfirmResultEvent(
                                reply_id=prompt.reply_id,
                                confirm_results=[
                                    ConfirmResult(
                                        request_id=prompt.id,
                                        confirmed=action != "cache_deny",
                                        tool_call=submitted,
                                    ),
                                ],
                            ),
                        )
                        await task
                        if action == "invalid":
                            replay = AssistantMsg(
                                agent.name,
                                [],
                                id=prompt.reply_id,
                            )
                            for event in (*initial, *agent.drain_events()):
                                replay.append_event(event)
                            self.assertEqual(
                                [
                                    (b.name, b.input)
                                    if b.type == "tool_call"
                                    else (b.name, b.state)
                                    for b in replay.content
                                ],
                                [("preview", "{}"), ("preview", "error")],
                            )
                        if action.startswith("cache"):
                            (result,) = agent.state.context[
                                -1
                            ].get_content_blocks(
                                "tool_result",
                            )
                            if action == "cache_miss_read_only":
                                self.assertEqual(result.state, "denied")
                            else:
                                self.assertEqual(
                                    result.output,
                                    'Tool "preview" denied by user.'
                                    if action == "cache_deny"
                                    else "final",
                                )
                states = [
                    b.state
                    for m in agent.state.context
                    for b in m.content
                    if b.type == "tool_result"
                ]
                expected = {
                    "edit": ([("preview", "final")], ["success"]),
                    "middleware_edit": ([("preview", "final")], ["success"]),
                    "rename": ([("render", "final")], ["success"]),
                    "inactive": ([], ["error"]),
                    "read_only": ([], ["denied"]),
                    "invalid": ([], ["error"]),
                    "cache_allow": ([], ["success"]),
                    "cache_deny": ([], ["denied"]),
                    "cache_rewrite_edit": ([], ["success"]),
                    "cache_miss": ([("preview", "final")], ["success"]),
                    "cache_miss_read_only": ([], ["denied"]),
                }
                self.assertEqual((calls, states), expected[action])

    async def test_auto_reviews_tool_middleware_arguments(self) -> None:
        """Review exactly the arguments forwarded to the realtime tool."""

        class LegacyPreview(FunctionTool):
            """A legacy implementation that delegates to ToolBase."""

            async def __call__(self, **kwargs: str) -> object:
                return await super().__call__(**kwargs)

        for allow, adapter, cached in product(
            (True, False),
            (FunctionTool, LegacyPreview),
            (False, True),
        ):
            with self.subTest(allow=allow, adapter=adapter, cached=cached):
                calls, reviewed = [], []

                def preview(value: str) -> str:
                    calls.append(value)
                    return value

                async def review(request: dict) -> PermissionReview:
                    value = request["action"]["arguments"]["value"]
                    reviewed.append(value)
                    return _review(
                        policy_violation=value == "draft!" and not allow,
                    )

                agent = RealtimeAgent(
                    "agent",
                    "Help",
                    ScriptedModel([[]]),
                    toolkit=Toolkit(
                        tools=[
                            adapter(
                                preview,
                                middlewares=[_AppendSuffix(), _Cache()]
                                if cached
                                else [_AppendSuffix()],
                            ),
                        ],
                    ),
                    permission_reviewer=review,
                    state=AgentState(
                        permission_context=PermissionContext(mode="auto"),
                    ),
                )
                async with agent:
                    await self._call(agent, "preview", '{"value":"draft"}')
                self.assertEqual(
                    (reviewed, calls),
                    (
                        ["draft", "draft!"],
                        ["draft!"] if allow and not cached else [],
                    ),
                )
                if cached:
                    body = [
                        e.delta
                        for e in agent.drain_events()
                        if isinstance(e, ToolResultTextDeltaEvent)
                    ]
                    (result,) = agent.state.context[-1].get_content_blocks(
                        "tool_result",
                    )
                    self.assertEqual(
                        (
                            "draft!" in body,
                            result.state,
                            any(
                                c.endswith("'draft!')")
                                for c in agent.model.calls
                            ),
                        ),
                        (allow, "success" if allow else "denied", allow),
                    )

    @staticmethod
    async def _call(
        agent: RealtimeAgent,
        name: str,
        arguments: str = "{}",
    ) -> None:
        """Run the actual realtime tool boundary within a current reply."""
        reply_id = f"reply-{len(agent.state.context)}"
        call = ToolCallBlock(id=reply_id, name=name, input=arguments)
        agent._reply_id = reply_id
        agent.state.context.append(
            AssistantMsg(id=reply_id, name=agent.name, content=[call]),
        )
        await agent._run_tool(reply_id, call)
