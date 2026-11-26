# -*- coding: utf-8 -*-
"""Approval routing, independent reviewers, and legacy policy migration."""
# pylint: disable=protected-access
import asyncio
import json
from itertools import product
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, PropertyMock, patch

from utils import MockModel

from agentscope.agent import Agent
from agentscope.app.storage._model._channel import SessionSettings
from agentscope.app.storage._model._permission import PermissionSettings
from agentscope.app.storage import ScheduleData, ScheduleRecord
from agentscope.classifier import ChoiceAnswer, ClassifierResponse
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionReview,
    PermissionRule,
)
from agentscope.permission.reviewer import (
    ChatPermissionReviewer,
    ClassifierPermissionReviewer,
)
from agentscope.state import AgentState
from agentscope.model import StructuredResponse
from agentscope.tool import ToolBase, ToolChunk, Toolkit
from agentscope.event import RequireUserConfirmEvent, ToolResultEndEvent


def _review(**updates: object) -> PermissionReview:
    """Build a complete assessment with deterministic defaults."""
    return PermissionReview.model_validate(
        {
            "risk_level": "low",
            "user_authorization": "high",
            "policy_violation": False,
            "rationale": "The user authorized this bounded edit.",
            **updates,
        },
    )


def _tool(
    behavior: PermissionBehavior = PermissionBehavior.PASSTHROUGH,
    read_only: bool = False,
    safety: bool = False,
) -> SimpleNamespace:
    """Expose the permission surface without running a real operation."""
    return SimpleNamespace(
        name="Change",
        approval_identity=lambda: ("Change",),
        description="Update a document.",
        input_schema={"type": "object"},
        check_permissions=AsyncMock(
            return_value=PermissionDecision(
                behavior=behavior,
                message="Tool decision",
                bypass_immune=safety,
            ),
        ),
        check_read_only=AsyncMock(return_value=read_only),
        generate_suggestions=AsyncMock(return_value=[]),
        get_permission_inputs=lambda value: [value],
        match_rule=AsyncMock(return_value=True),
    )


class PermissionRecheckTest(IsolatedAsyncioTestCase):
    """Exercise live policy and authorization checks around approval."""

    async def test_agent_refreshes_scope_after_read_only_check(self) -> None:
        """A pending call sees the current scope when classification ends."""
        agent = Agent("agent", "Help", MockModel())

        async def check(release_scope: bool) -> None:
            agent.state.permission_context = PermissionContext(read_only=True)
            started, resume = asyncio.Event(), asyncio.Event()
            tool = _tool()

            async def classify(_input: dict) -> bool:
                started.set()
                await resume.wait()
                return False

            tool.check_read_only.side_effect = classify
            with patch.object(
                AgentState,
                "effective_permission_context",
                new_callable=PropertyMock,
                side_effect=lambda: agent.state.permission_context,
            ) as policy:
                task = asyncio.create_task(
                    agent._check_permission(
                        ToolCallBlock(id="call", name="Change", input="{}"),
                        tool,
                        {},
                    ),
                )
                try:
                    await asyncio.wait_for(started.wait(), timeout=2)
                    self.assertEqual(policy.call_count, 1)
                    if release_scope:
                        agent.state.permission_context = PermissionContext()
                finally:
                    resume.set()
                decision = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(
                decision.behavior.value,
                "ask" if release_scope else "deny",
            )

        for release_scope in (False, True):
            with self.subTest(release_scope=release_scope):
                await check(release_scope)

    async def test_auto_recheck_generates_only_returned_suggestions(
        self,
    ) -> None:
        """Keep the approved suggestions until policy requires a new card."""
        for interactive in (True, False):
            with self.subTest(interactive=interactive):
                tool = _tool()
                grant = PermissionRule(
                    tool_name=tool.name,
                    behavior=PermissionBehavior.ALLOW,
                    source="suggested",
                    rule_content="original",
                )
                tool.generate_suggestions.return_value = [grant]
                engine = PermissionEngine(
                    PermissionContext(mode="auto", interactive=interactive),
                    reviewer=AsyncMock(return_value=_review()),
                )
                rechecks = []
                decision = await engine.check_permission(
                    tool,
                    {},
                    rechecks=rechecks,
                )
                (recheck,) = rechecks
                allowed = await recheck(decision)
                self.assertEqual(
                    (
                        allowed.behavior.value,
                        allowed.suggested_rules,
                        tool.generate_suggestions.await_count,
                    ),
                    ("allow", [grant], 1),
                )
                engine.add_rule(
                    grant.model_copy(
                        update={"behavior": PermissionBehavior.ASK},
                    ),
                )
                updated = grant.model_copy(update={"rule_content": "updated"})
                tool.generate_suggestions.return_value = [updated]
                pending = await recheck(decision)
                self.assertEqual(
                    (
                        pending.behavior.value,
                        pending.suggested_rules,
                        tool.generate_suggestions.await_count,
                    ),
                    ("ask" if interactive else "deny", [updated], 2),
                )

    async def test_auto_reuses_initial_authorization_snapshot(self) -> None:
        """Read evidence once before review and detect a later withdrawal."""
        messages = [UserMsg(name="user", content="Update the document.")]
        engine = PermissionEngine(PermissionContext(mode=PermissionMode.AUTO))
        reads_at_review = []
        with patch.object(
            engine,
            "_authorization_basis",
            wraps=engine._authorization_basis,
        ) as snapshot:

            async def review(_request: dict) -> PermissionReview:
                reads_at_review.append(snapshot.call_count)
                return _review()

            engine.reviewer = review
            rechecks = []
            decision = await engine.check_permission(
                _tool(),
                {},
                messages=messages,
                rechecks=rechecks,
            )
            messages.append(UserMsg(name="user", content="Stop the update."))
            (recheck,) = rechecks
            final = await recheck(decision)
        self.assertEqual(
            (reads_at_review, decision.behavior, final.behavior),
            ([1], PermissionBehavior.ALLOW, PermissionBehavior.ASK),
        )

    async def test_withdrawn_authorization_refreshes_suggestions(self) -> None:
        """A new approval request carries only the tool's current rules."""

        async def check_withdrawal(
            interactive: bool,
            during_review: bool,
        ) -> None:
            messages = [UserMsg("user", "Update the document.")]
            tool = _tool()
            original = PermissionRule(
                tool_name=tool.name,
                behavior=PermissionBehavior.ALLOW,
                rule_content="original",
                source="suggested",
            )
            current = original.model_copy(
                update={"rule_content": "current"},
            )
            tool.generate_suggestions.return_value = [original]

            def withdraw() -> None:
                messages.append(UserMsg("user", "Stop the update."))
                tool.generate_suggestions.return_value = [current]

            async def review(_request: dict) -> PermissionReview:
                if during_review:
                    withdraw()
                return _review()

            engine = PermissionEngine(
                PermissionContext(mode="auto", interactive=interactive),
                reviewer=review,
            )
            rechecks = []
            result = await engine.check_permission(
                tool,
                {},
                messages=messages,
                rechecks=rechecks,
            )
            if not during_review:
                self.assertEqual(result.suggested_rules, [original])
                withdraw()
                (recheck,) = rechecks
                result = await recheck(result)
            self.assertEqual(
                (
                    result.behavior.value,
                    result.suggested_rules,
                    result.review,
                    tool.generate_suggestions.await_count,
                ),
                ("ask" if interactive else "deny", [current], None, 2),
            )

        for interactive, during_review in product((True, False), repeat=2):
            with self.subTest(interactive=interactive, review=during_review):
                await check_withdrawal(interactive, during_review)

    async def test_suggestion_refresh_uses_live_interaction(self) -> None:
        """Resolve a withdrawn grant after the tool returns its new rules."""

        async def check_interaction(
            initial: bool,
            replace_context: bool,
            mode: PermissionMode,
        ) -> None:
            policy = PermissionContext(mode="auto", interactive=initial)
            engine = PermissionEngine(
                policy,
                reviewer=AsyncMock(return_value=_review()),
                context_provider=lambda: policy,
            )
            messages = [UserMsg("user", "Update the draft.")]
            tool = _tool()
            rechecks = []
            approved = await engine.check_permission(
                tool,
                {},
                messages=messages,
                rechecks=rechecks,
            )
            messages.append(UserMsg("user", "Stop the update."))
            rule = PermissionRule(
                tool_name=tool.name,
                behavior="allow",
                rule_content="current",
                source="suggested",
            )
            started, release = asyncio.Event(), asyncio.Event()

            async def suggest(_arguments: dict) -> list[PermissionRule]:
                started.set()
                await release.wait()
                return [rule]

            tool.generate_suggestions.side_effect = suggest
            (recheck,) = rechecks
            task = asyncio.create_task(recheck(approved))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                if replace_context:
                    policy = policy.model_copy()
                policy.interactive = not initial
                policy.mode = mode
            finally:
                release.set()
            result = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(
                (
                    result.behavior.value,
                    result.suggested_rules,
                    result.review,
                ),
                ("ask" if policy.interactive else "deny", [rule], None),
            )

        for initial, replace_context, mode in product(
            (True, False),
            (True, False),
            (PermissionMode.AUTO, PermissionMode.BYPASS),
        ):
            with self.subTest(
                initial=initial,
                replace=replace_context,
                mode=mode,
            ):
                await check_interaction(initial, replace_context, mode)

    async def test_unavailable_review_skips_authorization_snapshot(
        self,
    ) -> None:
        """Pending decisions without a usable reviewer need no snapshot."""
        for interactive, complete in product((True, False), repeat=2):
            with self.subTest(interactive=interactive, complete=complete):
                reviewer = AsyncMock(return_value=_review())
                engine = PermissionEngine(
                    PermissionContext(mode="auto", interactive=interactive),
                    reviewer=None if complete else reviewer,
                )
                rechecks = []
                with patch.object(
                    engine,
                    "_authorization_basis",
                    wraps=engine._authorization_basis,
                ) as snapshot:
                    decision = await engine.check_permission(
                        _tool(),
                        {},
                        evidence_complete=complete,
                        rechecks=rechecks,
                    )
                self.assertEqual(
                    (decision.behavior, snapshot.call_count, rechecks),
                    (
                        PermissionBehavior.ASK
                        if interactive
                        else PermissionBehavior.DENY,
                        0,
                        [],
                    ),
                )
                reviewer.assert_not_awaited()

    async def test_dispatch_recheck_detects_in_place_archive_edit(
        self,
    ) -> None:
        """Bind both reviewed and direct grants to original record values."""
        for direct in (False, True):
            with self.subTest(direct=direct):
                state = AgentState()
                state.retain_authorization([UserMsg("user", "Proceed.")])
                reviewer = AsyncMock(return_value=_review())
                engine = PermissionEngine(
                    PermissionContext(mode="auto"),
                    reviewer=None if direct else reviewer,
                    authorization_provider=state.permission_review_context,
                )
                rechecks = []
                decision = await engine.check_permission(
                    _tool(read_only=direct),
                    {},
                    rechecks=rechecks,
                )
                state.authorization_evidence[0].text = "Stop."
                (recheck,) = rechecks
                final = await recheck(decision)
                self.assertEqual(
                    (decision.behavior, final.behavior, reviewer.await_count),
                    (
                        PermissionBehavior.ALLOW,
                        PermissionBehavior.ASK,
                        int(not direct),
                    ),
                )


class PermissionReviewTest(IsolatedAsyncioTestCase):
    """Exercise policy routing with complete expected decisions."""

    async def test_pending_request_routes_by_mode(self) -> None:
        """Only AUTO invokes a reviewer for the same pending operation."""
        expected = {
            PermissionMode.ASK: PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message="Permission required for Change",
                decision_reason="Mode: ask",
                suggested_rules=[],
            ),
            PermissionMode.AUTO: PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Automatic review: The user authorized this "
                "bounded edit.",
                decision_reason="Automatic review: The user authorized this "
                "bounded edit.",
                suggested_rules=[],
                review=_review(),
            ),
            PermissionMode.BYPASS: PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Permission granted for Change (bypass mode)",
                decision_reason="Bypass mode approves pending requests",
                suggested_rules=[],
            ),
        }
        self.assertEqual(
            [mode.value for mode in PermissionMode],
            ["ask", "auto", "bypass"],
        )
        for mode in PermissionMode:
            with self.subTest(mode=mode):
                reviewer = AsyncMock(return_value=_review())
                engine = PermissionEngine(
                    PermissionContext(mode=mode),
                    reviewer=reviewer,
                )
                self.assertEqual(
                    await engine.check_permission(_tool(), {"text": "new"}),
                    expected[mode],
                )
                self.assertEqual(
                    reviewer.await_count,
                    int(mode == PermissionMode.AUTO),
                )

    async def test_only_approved_actions_execute(self) -> None:
        """Review results guard the actual tool execution boundary."""
        executed = []

        class UpdateTool(ToolBase):
            """Record actual side effects without external services."""

            name = "Update"
            description = "Update a document."
            input_schema = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }
            is_read_only = False
            is_concurrency_safe = False

            async def check_permissions(
                self,
                tool_input: dict,
                context: PermissionContext,
            ) -> PermissionDecision:
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message="Approval needed",
                )

            async def call(self, text: str) -> ToolChunk:
                executed.append(text)
                return ToolChunk(content=[TextBlock(text="Updated")])

        for review, expected in (
            (_review(), "success"),
            (_review(risk_level="critical"), "denied"),
            ({}, "asking"),
        ):
            with self.subTest(expected=expected):
                executed.clear()
                agent = Agent(
                    name="agent",
                    system_prompt="Help",
                    model=MockModel(),
                    toolkit=Toolkit(tools=[UpdateTool()]),
                    state=AgentState(
                        permission_context=PermissionContext(
                            mode=PermissionMode.AUTO,
                        ),
                    ),
                    permission_reviewer=AsyncMock(return_value=review),
                )
                events = [
                    event
                    async for event in agent._execute_tool_call(
                        ToolCallBlock(
                            id="call",
                            name="Update",
                            input='{"text":"new"}',
                        ),
                    )
                ]
                outcome = [
                    "asking"
                    if isinstance(event, RequireUserConfirmEvent)
                    else event.state
                    for event in events
                    if isinstance(
                        event,
                        (RequireUserConfirmEvent, ToolResultEndEvent),
                    )
                ]
                self.assertEqual(
                    (outcome, executed),
                    ([expected], ["new"] if expected == "success" else []),
                )

    async def test_rule_precedence_and_tool_denials(self) -> None:
        """Hard denies win, ASK routes by mode, and allow skips review."""
        for mode, deny, ask, allow, tool_deny in product(
            PermissionMode,
            (False, True),
            (False, True),
            (False, True),
            (False, True),
        ):
            with self.subTest(
                mode=mode,
                deny=deny,
                ask=ask,
                allow=allow,
                tool_deny=tool_deny,
            ):
                reviewer = AsyncMock(return_value=_review())
                engine = PermissionEngine(
                    PermissionContext(mode=mode),
                    reviewer=reviewer,
                )
                for enabled, behavior in (
                    (allow, PermissionBehavior.ALLOW),
                    (ask, PermissionBehavior.ASK),
                    (deny, PermissionBehavior.DENY),
                ):
                    if enabled:
                        engine.add_rule(
                            PermissionRule(
                                tool_name="Change",
                                rule_content=None,
                                behavior=behavior,
                                source="test",
                            ),
                        )
                decision = await engine.check_permission(
                    _tool(
                        PermissionBehavior.DENY
                        if tool_deny
                        else PermissionBehavior.PASSTHROUGH,
                    ),
                    {},
                )
                denied = deny or tool_deny
                pending = not denied and (ask or not allow)
                expected = (
                    PermissionBehavior.DENY
                    if denied
                    else (
                        PermissionBehavior.ASK
                        if pending and mode == PermissionMode.ASK
                        else PermissionBehavior.ALLOW
                    )
                )
                self.assertEqual(
                    (decision.behavior, reviewer.await_count),
                    (expected, int(pending and mode == PermissionMode.AUTO)),
                )

    async def test_read_only_never_overrides_tool_deny(self) -> None:
        """A tool's read-only label cannot hide its hard denial."""
        for mode in PermissionMode:
            tool = _tool(PermissionBehavior.DENY, read_only=True)
            reviewer = AsyncMock()
            decision = await PermissionEngine(
                PermissionContext(mode=mode),
                reviewer=reviewer,
            ).check_permission(tool, {})
            self.assertEqual(decision, tool.check_permissions.return_value)
            reviewer.assert_not_awaited()

    async def test_read_only_scope_is_independent_of_reviewer(self) -> None:
        """Even BYPASS cannot grant writes in a read-only scope."""
        for mode in PermissionMode:
            engine = PermissionEngine(
                PermissionContext(mode=mode, read_only=True),
            )
            decision = await engine.check_permission(_tool(), {})
            self.assertEqual(
                decision,
                PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="Permission denied for Change (read-only scope)",
                    decision_reason="Read-only scope does not allow "
                    "modifications",
                ),
            )

    async def test_safety_ask_reaches_reviewer_despite_allow_rule(
        self,
    ) -> None:
        """A safety ASK still needs review when an allow rule matches."""
        reviewer = AsyncMock(return_value=_review(risk_level="critical"))
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=reviewer,
        )
        engine.add_rule(
            PermissionRule(
                tool_name="Change",
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        decision = await engine.check_permission(
            _tool(PermissionBehavior.ASK, safety=True),
            {},
        )
        self.assertEqual(
            (decision.behavior, decision.review, reviewer.await_count),
            (PermissionBehavior.DENY, _review(risk_level="critical"), 1),
        )
        self.assertIn("Do not retry", decision.message)

    async def test_review_failures_never_allow(self) -> None:
        """Missing, invalid, refused, and failed reviews require approval."""
        for interactive, reviewer in product(
            (True, False),
            (
                None,
                AsyncMock(return_value={}),
                AsyncMock(side_effect=ValueError("refused")),
            ),
        ):
            decision = await PermissionEngine(
                PermissionContext(
                    mode=PermissionMode.AUTO,
                    interactive=interactive,
                ),
                reviewer=reviewer,
            ).check_permission(_tool(), {})
            self.assertEqual(
                (decision.behavior, decision.review),
                (
                    PermissionBehavior.ASK
                    if interactive
                    else PermissionBehavior.DENY,
                    None,
                ),
            )

    async def test_timeout_and_cancellation(self) -> None:
        """Timeout falls back safely; cancellation escapes the review."""

        async def blocked(request: dict) -> PermissionReview:
            del request
            await asyncio.Event().wait()
            return _review()

        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=blocked,
            review_timeout=0.01,
        )
        decision = await engine.check_permission(_tool(), {})
        self.assertEqual(
            (decision.behavior, decision.decision_reason),
            (PermissionBehavior.ASK, "Automatic review failed: TimeoutError"),
        )
        engine.reviewer = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await engine.check_permission(_tool(), {})

    async def test_exact_action_and_visible_context(self) -> None:
        """Review keeps tool evidence and roles, excluding hidden reasoning."""
        reviewer = AsyncMock(return_value=_review())
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=reviewer,
        )
        messages = [
            UserMsg(name="user", content="Update the document."),
            AssistantMsg(
                name="agent",
                content=[
                    ThinkingBlock(thinking="private reasoning"),
                    ToolResultBlock(id="r", name="Read", output="old text"),
                ],
            ),
        ]
        arguments = {"document": {"text": "exact new text"}}
        await engine.check_permission(_tool(), arguments, messages)
        request = reviewer.call_args.args[0]
        self.assertEqual(
            request,
            {
                "action": {
                    "tool_name": "Change",
                    "description": "Update a document.",
                    "input_schema": {"type": "object"},
                    "arguments": arguments,
                },
                "approval_reason": "Mode: auto",
                "approval_message": "Permission required for Change",
                "bypass_immune": False,
                "tool_decision": {
                    "behavior": "passthrough",
                    "message": "Tool decision",
                    "decision_reason": None,
                    "bypass_immune": False,
                },
                "working_directories": [],
                "transcript": [
                    {
                        "role": "user",
                        "name": "user",
                        "content": [
                            {"type": "text", "text": "Update the document."},
                        ],
                    },
                    {
                        "role": "assistant",
                        "name": "agent",
                        "content": [
                            {
                                "type": "tool_result",
                                "name": "Read",
                                "output": "old text",
                            },
                        ],
                    },
                ],
            },
        )
        request["action"]["arguments"]["document"]["text"] = "mutated"
        self.assertEqual(arguments, {"document": {"text": "exact new text"}})

    async def test_review_preserves_tool_safety_and_ask_rule(self) -> None:
        """A matched rule cannot erase a tool's message-only risk evidence."""
        for matched_rule, safety, reason in product(
            (False, True),
            (False, True),
            (None, "Sensitive destination"),
        ):
            with self.subTest(rule=matched_rule, safety=safety, reason=reason):
                context = PermissionContext(mode=PermissionMode.AUTO)
                reviewer = AsyncMock(
                    return_value=_review(risk_level="critical"),
                )
                engine = PermissionEngine(context, reviewer=reviewer)
                if matched_rule:
                    engine.add_rule(
                        PermissionRule(
                            tool_name="Change",
                            rule_content="*",
                            behavior=PermissionBehavior.ASK,
                            source="session",
                        ),
                    )
                tool = _tool(PermissionBehavior.ASK, safety=safety)
                risk_message = (
                    "This operation uploads private keys to an external host"
                )
                tool.check_permissions.return_value.message = risk_message
                tool.check_permissions.return_value.decision_reason = reason
                decision = await engine.check_permission(tool, {})
                self.assertEqual(decision.behavior, PermissionBehavior.DENY)
                self.assertEqual(
                    reviewer.call_args.args[0],
                    {
                        "action": {
                            "tool_name": "Change",
                            "description": "Update a document.",
                            "input_schema": {"type": "object"},
                            "arguments": {},
                        },
                        "approval_reason": "Rule: *"
                        if matched_rule
                        else reason,
                        "approval_message": "Permission required for Change"
                        if matched_rule
                        else risk_message,
                        "bypass_immune": False if matched_rule else safety,
                        "tool_decision": {
                            "behavior": "ask",
                            "message": risk_message,
                            "decision_reason": reason,
                            "bypass_immune": safety,
                        },
                        "working_directories": [],
                        "transcript": [],
                    },
                )
                tool.check_permissions.assert_awaited_once()

    async def test_agent_uses_default_chat_reviewer_and_current_context(
        self,
    ) -> None:
        """Replacing state policy must not leave the engine on stale rules."""
        model = MockModel(
            context_size=32768,
            mock_structured_response=StructuredResponse(
                content=_review().model_dump(),
            ),
        )
        agent = Agent(name="agent", system_prompt="Help", model=model)
        self.assertIsInstance(agent._engine.reviewer, ChatPermissionReviewer)
        agent.state.permission_context = PermissionContext(
            mode=PermissionMode.AUTO,
        )
        agent.state.context = [UserMsg(name="user", content="Update it.")]
        decision = await agent._check_permission_impl(
            ToolCallBlock(id="call", name="Change", input="{}"),
            _tool(),
            {},
        )
        self.assertEqual(
            (decision.behavior, decision.review),
            (PermissionBehavior.ALLOW, _review()),
        )
        reviewer = AsyncMock(return_value=_review(risk_level="critical"))
        agent = Agent(
            name="agent",
            system_prompt="Help",
            model=model,
            permission_reviewer=reviewer,
            state=AgentState(
                permission_context=PermissionContext(mode=PermissionMode.AUTO),
            ),
        )
        decision = await agent._check_permission_impl(
            ToolCallBlock(id="call", name="Change", input="{}"),
            _tool(),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
        self.assertEqual(
            reviewer.call_args.args[0]["application_instructions"],
            "Help",
        )

    async def test_chat_adapter_uses_separate_structured_call(self) -> None:
        """The review has its own system prompt and exact JSON evidence."""
        model = MockModel(context_size=32768)
        model.generate_structured_output = AsyncMock(
            return_value=SimpleNamespace(content=_review().model_dump()),
        )
        request = {"action": {"arguments": {"text": "hello"}}}
        result = await ChatPermissionReviewer(model)(request)
        self.assertEqual(result, _review())
        kwargs = model.generate_structured_output.call_args.kwargs
        self.assertEqual(kwargs["structured_model"], PermissionReview)
        self.assertEqual(
            [message.role for message in kwargs["messages"]],
            ["system", "user"],
        )
        self.assertEqual(
            json.loads(kwargs["messages"][1].content[0].text),
            request,
        )

    async def test_classifier_adapter_and_uncertain_results(self) -> None:
        """Jev-compatible choices share the outcome policy and fail closed."""
        answers = {
            name: ChoiceAnswer(
                choice=value,
                confidence=0.95,
                probabilities={value: 0.95},
            )
            for name, value in (
                ("risk_level", "low"),
                ("user_authorization", "high"),
                ("policy_violation", "no"),
            )
        }
        model = AsyncMock(
            return_value=ClassifierResponse(model="jev", content=answers),
        )
        reviewer = ClassifierPermissionReviewer(model)
        self.assertEqual(
            await reviewer({"action": "test"}),
            _review(
                rationale="Classifier assessment: risk=low, "
                "authorization=high, policy_violation=no.",
            ),
        )
        self.assertEqual(model.call_args.kwargs["state"], {"action": "test"})
        for confidence in (0.3, float("nan"), 1.5):
            answers["risk_level"].confidence = confidence
            with self.assertRaises(ValueError):
                await reviewer({})
        answers.pop("risk_level")
        with self.assertRaises(ValueError):
            await reviewer({})

    def test_outcome_policy(self) -> None:
        """Risk, authorization, and explicit policy violations are separate."""
        for risk, authorization, violation in product(
            ("low", "medium", "high", "critical"),
            ("high", "medium", "low", "unknown"),
            (False, True),
        ):
            result = _review(
                risk_level=risk,
                user_authorization=authorization,
                policy_violation=violation,
            )
            self.assertEqual(
                result.allowed,
                not violation
                and (
                    risk in ("low", "medium")
                    or risk == "high"
                    and authorization in ("high", "medium")
                ),
            )

    def test_legacy_context_and_settings_round_trip(self) -> None:
        """Old modes retain restrictions and grants after serialization."""
        for mode, read_only, edits, interactive in (
            ("default", False, False, True),
            ("accept_edits", False, True, True),
            ("explore", True, False, True),
            ("dont_ask", False, True, False),
        ):
            with self.subTest(mode=mode):
                context = PermissionContext(mode=mode)
                expected = {
                    "mode": "ask",
                    "read_only": read_only,
                    "allow_workspace_edits": edits,
                    "interactive": interactive,
                    "working_directories": {},
                    "allow_rules": {},
                    "deny_rules": {},
                    "ask_rules": {},
                }
                self.assertEqual(context.model_dump(mode="json"), expected)
                self.assertEqual(
                    PermissionContext.model_validate_json(
                        context.model_dump_json(),
                    ),
                    context,
                )
                settings = PermissionSettings(permission_mode=mode)
                self.assertEqual(settings.to_permission_context(), context)
                channel = SessionSettings(
                    chat_model_config={},
                    permission_mode=mode,
                )
                self.assertEqual(channel.to_permission_context(), context)

    def test_schedule_defaults_and_migration(self) -> None:
        """Schedules remain unattended even with a canonical new mode."""
        base = {
            "name": "job",
            "cron_expression": "0 * * * *",
            "chat_model_config": {
                "credential_id": "test",
                "model": "test",
                "type": "test",
                "parameters": {},
            },
        }
        for mode in ("auto", "ask", "bypass", "dont_ask", "explore"):
            record = ScheduleRecord(
                user_id="user",
                agent_id="agent",
                data=ScheduleData(**base, permission_mode=mode),
            )
            state = AgentState()
            record.apply_to_session(state, new_session=True)
            context = state.permission_context
            self.assertEqual(
                (
                    context.mode.value,
                    context.read_only,
                    context.allow_workspace_edits,
                    context.interactive,
                ),
                (
                    mode if mode in ("auto", "ask", "bypass") else "ask",
                    mode == "explore",
                    mode == "dont_ask",
                    False,
                ),
            )
