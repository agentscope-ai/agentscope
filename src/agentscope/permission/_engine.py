# -*- coding: utf-8 -*-
"""Shared tool policy evaluation and approval dispatch."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from functools import cache, partial
from typing import Any, Awaitable, Callable, List, TYPE_CHECKING

from ._context import PermissionContext
from ._rule import PermissionRule
from ._decision import PermissionDecision, PermissionBehavior
from ._types import PermissionMode
from ._review import (
    PermissionReview,
    PermissionReviewer,
    ReviewInputTooLarge,
    ReviewInstructions,
    review_json_size,
)
from ._evidence import (
    AuthorizationEvidence,
    authorization_key,
    has_unreviewable_input,
    has_verified_source,
    ScheduleAuthorization,
    retain_authorization_evidence,
)
from .._utils._common import _execute_async_or_sync_func
from .._logging import logger

if TYPE_CHECKING:
    from ..message import Msg
    from ..tool import ToolBase


InstructionSource = str | Callable[[], Awaitable[str | ReviewInstructions]]

ApprovalRecheck = Callable[[PermissionDecision], Awaitable[PermissionDecision]]
_RuleInputs = Callable[[], list[dict[str, Any]]]


async def _instruction_fields(source: InstructionSource | None) -> dict:
    """Keep prompt provenance when loading and rechecking instructions."""
    value = await source() if callable(source) else source
    if value is None:
        return {}
    if isinstance(value, str):
        return {"application_instructions": value}
    fields: dict[str, Any] = {
        "application_instructions": value["application_instructions"],
    }
    if "untrusted_context" in value:
        fields["untrusted_context"] = list(value["untrusted_context"])
    return fields


class PermissionEngine:
    """Enforce current policy around independent approval review."""

    def __init__(
        self,
        context: PermissionContext,
        reviewer: PermissionReviewer | None = None,
        review_timeout: float = 300.0,
        context_provider: Callable[[], PermissionContext] | None = None,
        authorization_provider: Callable[[], dict[str, Any]] | None = None,
        rule_context_provider: Callable[[], PermissionContext] | None = None,
    ) -> None:
        """Bind policy and an optional independent approval reviewer."""
        if review_timeout <= 0:
            raise ValueError("review_timeout must be positive")
        self.context = context
        self.reviewer = reviewer
        self.review_timeout = review_timeout
        self.context_provider = context_provider
        self.authorization_provider = authorization_provider
        self.rule_context_provider = rule_context_provider

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a rule to its corresponding policy layer."""
        context = (
            self.rule_context_provider()
            if self.rule_context_provider
            else self.context
        )
        rules = {
            PermissionBehavior.ALLOW: context.allow_rules,
            PermissionBehavior.DENY: context.deny_rules,
            PermissionBehavior.ASK: context.ask_rules,
        }.get(rule.behavior)
        if rules is not None:
            rules.setdefault(rule.tool_name, []).append(rule)

    def add_confirmed_rules(
        self,
        tool: "ToolBase",
        rules: list[PermissionRule],
    ) -> None:
        """Bind grants from a confirmation card to its actual target."""
        fingerprint = tool.confirmation_identity()
        for rule in rules:
            if rule.behavior == PermissionBehavior.ALLOW:
                if rule.tool_name != tool.name:
                    continue
                rule = rule.model_copy(
                    update={"confirmation_target": fingerprint},
                )
            self.add_rule(rule)

    async def check_permission(
        self,
        tool: "ToolBase",
        tool_input: dict[str, Any],
        messages: list["Msg"] | None = None,
        instructions: InstructionSource | None = None,
        confirmed: bool = False,
        authorization_evidence: list[AuthorizationEvidence] | None = None,
        evidence_complete: bool = True,
        schedule_authorization: ScheduleAuthorization | None = None,
        rechecks: list[ApprovalRecheck] | None = None,
    ) -> PermissionDecision:
        """Enforce policy before resolving any pending approval.

        An instruction loader runs only for pending AUTO review, within its
        deadline and safe failure handling, after current hard limits pass.
        A context provider follows state replacement during review; successful
        reviews must pass the current hard limits before execution.
        Changing approval mode discards the old review and uses current policy.
        Changed ASK requirements invalidate approval without another review.
        Middleware callers may collect per-call rechecks for a final ALLOW.
        """
        if self.context_provider is not None:
            self.context = self.context_provider()
        identity = (
            tool.approval_identity()
            if self.context.mode == PermissionMode.AUTO and not confirmed
            else None
        )
        decision, tool_decision = await self._evaluate(tool, tool_input)
        if decision.behavior == PermissionBehavior.ASK:
            decision = await self._with_suggestions(tool, tool_input, decision)
        direct_grant = decision.behavior == PermissionBehavior.ALLOW
        capture_grant = (
            rechecks is not None
            and not confirmed
            and self.context.mode == PermissionMode.AUTO
        )
        if direct_grant and not confirmed:
            decision = self.recheck_tool_identity(decision, tool, identity)
            if decision.behavior != PermissionBehavior.ALLOW:
                return decision
        if decision.behavior != PermissionBehavior.ASK and not (
            direct_grant and capture_grant
        ):
            return decision
        if confirmed:
            return replace(
                decision,
                behavior=PermissionBehavior.ALLOW,
                message=(
                    "User confirmation accepted after current policy checks"
                ),
            )
        if self.context.mode == PermissionMode.AUTO:
            basis = self._approval_basis(tool.name, decision, tool_decision)
            authorization = (
                self.authorization_provider()
                if self.authorization_provider is not None
                else {
                    "messages": messages if messages is not None else [],
                    "authorization_evidence": authorization_evidence
                    if authorization_evidence is not None
                    else [],
                    "evidence_complete": evidence_complete,
                    "schedule_authorization": schedule_authorization,
                }
            )
            reviewed_fields = None
            if direct_grant:
                authority = self._authorization_basis(authorization)
            else:
                instruction_fields: dict[str, Any] = {}
                decision, authority = await self._review(
                    tool,
                    tool_input,
                    decision,
                    instructions,
                    tool_decision,
                    authorization,
                    instruction_fields,
                )
                if decision.review is not None and decision.review.allowed:
                    reviewed_fields = instruction_fields
                decision = await self._recheck_approval(
                    decision,
                    tool,
                    tool_input,
                    identity,
                    basis,
                    authority,
                    authorization,
                )
            if (
                rechecks is not None
                and decision.behavior == PermissionBehavior.ALLOW
            ):
                rechecks.append(
                    partial(
                        self._recheck_approval,
                        tool=tool,
                        tool_input=deepcopy(tool_input),
                        identity=identity,
                        basis=basis,
                        authority=authority,
                        authorization=authorization,
                        instructions=instructions,
                        reviewed_fields=reviewed_fields,
                    ),
                )
            return decision
        return self._resolve_pending(tool, decision)

    async def _recheck_approval(
        self,
        decision: PermissionDecision,
        tool: "ToolBase",
        tool_input: dict[str, Any],
        identity: tuple | None,
        basis: tuple,
        authority: tuple | None,
        authorization: dict[str, Any],
        instructions: InstructionSource | None = None,
        reviewed_fields: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Rebind policy and invalidate a grant when its basis changes."""
        instruction_change = None
        if reviewed_fields is not None:
            try:
                async with asyncio.timeout(self.review_timeout):
                    current_fields = await _instruction_fields(instructions)
                if current_fields != reviewed_fields:
                    instruction_change = "Approval instructions changed"
            except Exception as error:
                instruction_change = (
                    f"Instruction check failed: {type(error).__name__}"
                )
        if self.context_provider is not None:
            self.context = self.context_provider()
        if (
            decision.behavior == PermissionBehavior.ALLOW
            or self.context.mode != PermissionMode.AUTO
        ):
            current, current_tool = await self._evaluate(tool, tool_input)
            replacement = None
            if (
                current.behavior == PermissionBehavior.DENY
                or self.context.mode != PermissionMode.AUTO
            ):
                replacement = current
            elif current.behavior == PermissionBehavior.ASK and (
                decision.review is None
                or not decision.review.allowed
                or basis
                != self._approval_basis(
                    tool.name,
                    current,
                    current_tool,
                )
            ):
                replacement = replace(
                    current,
                    decision_reason=(
                        "Approval requirements changed during automatic "
                        "review; user approval is required. "
                        f"{current.decision_reason or ''}"
                    ).strip(),
                    review=None,
                )
            if replacement is not None:
                decision = replacement
                if decision.behavior == PermissionBehavior.ASK:
                    decision = await self._with_suggestions(
                        tool,
                        tool_input,
                        decision,
                    )
        if (
            decision.behavior == PermissionBehavior.ALLOW
            and self.context.mode == PermissionMode.AUTO
            and (
                instruction_change
                or authority
                != self._authorization_basis(
                    self.authorization_provider()
                    if self.authorization_provider is not None
                    else authorization,
                )
            )
        ):
            decision = replace(
                decision,
                behavior=PermissionBehavior.ASK,
                message="Authorization changed; user approval is required",
                decision_reason=instruction_change
                or (
                    "Automatic approval invalidated by "
                    "changed authorization context"
                ),
                review=None,
                suggested_rules=None,
            )
        if (
            decision.behavior == PermissionBehavior.ASK
            and decision.suggested_rules is None
        ):
            decision = await self._with_suggestions(tool, tool_input, decision)
            # A withdrawn grant can only go to a user or be denied.
            return self._resolve_unattended(tool, decision)
        decision = self._resolve_pending(tool, decision)
        return self.recheck_tool_identity(decision, tool, identity)

    def recheck_tool_identity(
        self,
        decision: PermissionDecision,
        tool: "ToolBase",
        identity: tuple | None,
    ) -> PermissionDecision:
        """Bind an AUTO grant to the live target at the dispatch boundary."""
        if decision.behavior != PermissionBehavior.ALLOW:
            return decision
        policy = (
            self.context_provider() if self.context_provider else self.context
        )
        if (
            policy.mode != PermissionMode.AUTO
            or tool.approval_identity() == identity
        ):
            return decision
        reason = "Tool identity changed during automatic approval"
        return PermissionDecision(
            behavior=PermissionBehavior.ASK
            if policy.interactive
            else PermissionBehavior.DENY,
            message=reason,
            decision_reason=reason,
            bypass_immune=True,
        )

    def _resolve_pending(
        self,
        tool: "ToolBase",
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Apply the current mode and unattended fallback."""
        if decision.behavior != PermissionBehavior.ASK:
            return decision
        if self.context.mode == PermissionMode.BYPASS:
            return replace(
                decision,
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool.name} (bypass mode)",
                decision_reason="Bypass mode approves pending requests",
            )
        return self._resolve_unattended(tool, decision)

    def _resolve_unattended(
        self,
        tool: "ToolBase",
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Deny a pending request if a user is no longer available."""
        policy = (
            self.context_provider() if self.context_provider else self.context
        )
        if not policy.interactive:
            return replace(
                decision,
                behavior=PermissionBehavior.DENY,
                message=(
                    f"Permission denied for {tool.name}: "
                    "no user is available to approve this operation"
                ),
                decision_reason=(
                    f"Unattended approval denied. {decision.decision_reason}"
                ),
            )
        return decision

    def _approval_basis(
        self,
        tool_name: str,
        decision: PermissionDecision,
        tool_decision: PermissionDecision | None,
    ) -> tuple[
        PermissionDecision,
        PermissionDecision | None,
        list[PermissionRule],
    ]:
        """Snapshot requirements, excluding nonbinding rule suggestions."""
        return deepcopy(
            (
                replace(decision, suggested_rules=None, review=None),
                replace(tool_decision, suggested_rules=None, review=None)
                if tool_decision is not None
                else None,
                self.context.ask_rules.get(tool_name, []),
            ),
        )

    async def _evaluate(
        self,
        tool: "ToolBase",
        tool_input: dict[str, Any],
    ) -> tuple[PermissionDecision, PermissionDecision | None]:
        """Evaluate restrictions, rules, and tool checks for every mode."""

        @cache
        def operations() -> list[dict[str, Any]]:
            return tool.get_permission_inputs(tool_input)

        deny = await self._check_deny_rules(tool, tool_input, operations)
        if deny:
            return deny, None

        def read_only_denial() -> PermissionDecision:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Permission denied for {tool.name} (read-only scope)",
                decision_reason="Read-only scope does not allow modifications",
            )

        async def classify_read_only() -> tuple[
            bool,
            PermissionDecision | None,
        ]:
            result = await tool.check_read_only(tool_input)
            if self.context_provider is not None:
                self.context = self.context_provider()
            if self.context.read_only and not result:
                return result, read_only_denial()
            return result, await self._check_deny_rules(
                tool,
                tool_input,
                operations,
            )

        read_only = None
        if self.context.read_only:
            read_only, deny = await classify_read_only()
            if deny:
                return deny, None

        # Hard tool denials apply even to read-only tools and explicit grants.
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior == PermissionBehavior.DENY:
            return tool_decision, tool_decision

        # The tool hook can suspend while the session's hard limits change.
        if self.context_provider is not None:
            self.context = self.context_provider()
        if self.context.read_only and read_only is None:
            read_only, deny = await classify_read_only()
        else:
            deny = await self._check_deny_rules(tool, tool_input, operations)
        if deny:
            return deny, None

        async def current_decision() -> PermissionDecision | None:
            ask = await self._check_ask_rules(tool, tool_input, operations)
            if ask:
                return ask
            if (
                tool_decision.behavior == PermissionBehavior.ASK
                and tool_decision.bypass_immune
            ):
                return tool_decision
            allow = await self._check_allow_rules(tool, tool_input, operations)
            if allow:
                return allow
            if tool_decision.behavior == PermissionBehavior.ALLOW:
                return tool_decision
            return None

        decision = await current_decision()
        if read_only is None and (decision is None or self.context.read_only):
            read_only, deny = await classify_read_only()
            if deny:
                return deny, None
            decision = await current_decision()
        if self.context.read_only and not read_only:
            return read_only_denial(), None
        if decision is not None:
            return decision, tool_decision
        if read_only:
            return (
                PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=(
                        f"Permission granted for {tool.name} "
                        "(read-only invocation)"
                    ),
                    decision_reason="Read-only operations are auto-allowed",
                ),
                tool_decision,
            )

        pending = tool_decision
        if pending.behavior != PermissionBehavior.ASK:
            pending = PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required for {tool.name}",
                decision_reason=f"Mode: {self.context.mode.value}",
            )
        return pending, tool_decision

    async def _with_suggestions(
        self,
        tool: "ToolBase",
        tool_input: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Attach rule suggestions without mutating a tool's cached result."""
        return replace(
            decision,
            suggested_rules=await self._generate_suggestions(tool, tool_input),
        )

    async def _review(
        self,
        tool: "ToolBase",
        tool_input: dict[str, Any],
        decision: PermissionDecision,
        instructions: InstructionSource | None,
        tool_decision: PermissionDecision | None,
        authorization: dict[str, Any],
        instruction_fields: dict[str, Any],
    ) -> tuple[PermissionDecision, tuple | None]:
        """Return the review decision and the authority it examined."""
        messages = authorization.get("messages", [])
        authorization_evidence = authorization.get("authorization_evidence")
        evidence_complete = authorization.get("evidence_complete", True)
        schedule_authorization = authorization.get("schedule_authorization")
        if (
            not evidence_complete
            or has_unreviewable_input(messages)
            or (
                schedule_authorization is not None
                and not schedule_authorization.complete
            )
        ):
            decision = replace(
                decision,
                message="Original authorization history is incomplete; "
                "user approval is required",
                decision_reason=(
                    "Original instructions are missing or unreadable"
                ),
            )
            return decision, None
        if self.reviewer is None:
            decision = replace(
                decision,
                message="Automatic review unavailable: no reviewer configured",
                decision_reason="No automatic permission reviewer configured",
            )
            return decision, None
        authority = self._authorization_basis(authorization)
        request = {
            "action": {
                "tool_name": tool.name,
                "description": tool.description,
                "input_schema": deepcopy(tool.input_schema),
                "arguments": deepcopy(tool_input),
            },
            "approval_reason": decision.decision_reason,
            "approval_message": decision.message,
            "bypass_immune": decision.bypass_immune,
            "tool_decision": (
                {
                    "behavior": tool_decision.behavior.value,
                    "message": tool_decision.message,
                    "decision_reason": tool_decision.decision_reason,
                    "bypass_immune": tool_decision.bypass_immune,
                }
                if tool_decision is not None
                else None
            ),
            "working_directories": list(self.context.working_directories),
            "transcript": self._review_transcript(
                messages or [],
                authorization_evidence,
            ),
        }
        if authorization_evidence:
            request["authorization_evidence"] = [
                item.model_dump() for item in authorization_evidence
            ]
        if schedule_authorization is not None:
            request[
                "schedule_authorization"
            ] = schedule_authorization.model_dump()
        try:
            async with asyncio.timeout(self.review_timeout):
                instruction_fields.update(
                    await _instruction_fields(instructions),
                )
                request.update(deepcopy(instruction_fields))
                review = await self.reviewer(request)
                review = PermissionReview.model_validate(review)
                if review.allowed:
                    current_instructions = await _instruction_fields(
                        instructions,
                    )
                    current_authorization = (
                        self.authorization_provider()
                        if self.authorization_provider is not None
                        else authorization
                    )
                    if (
                        authority
                        != self._authorization_basis(
                            current_authorization,
                        )
                        or current_instructions != instruction_fields
                    ):
                        decision = replace(
                            decision,
                            message="Authorization changed during automatic "
                            "review; user approval is required",
                            decision_reason="Automatic approval invalidated "
                            "by changed authorization context",
                            review=None,
                            suggested_rules=None,
                        )
                        return decision, authority
        except ReviewInputTooLarge as error:
            logger.info("Automatic review skipped: %s", error)
            decision = replace(
                decision,
                message="Approval evidence exceeds the review budget",
                decision_reason=str(error),
            )
            return decision, authority
        except Exception as error:
            # Cancellation is a BaseException and must propagate.
            decision = replace(
                decision,
                message="Automatic review unavailable; approval is required",
                decision_reason=(
                    f"Automatic review failed: {type(error).__name__}"
                ),
            )
            return decision, authority
        behavior = (
            PermissionBehavior.ALLOW
            if review.allowed
            else PermissionBehavior.DENY
        )
        message = f"Automatic review: {review.rationale}"
        if not review.allowed:
            message += (
                " Do not retry the same action through another tool or "
                "workaround. Use a materially safer alternative or ask "
                "the user to clarify authorization."
            )
        decision = replace(
            decision,
            behavior=behavior,
            message=message,
            decision_reason=f"Automatic review: {review.rationale}",
            review=review,
        )
        return decision, authority

    def dispatch_basis(self, *, auto_only: bool = False) -> tuple | None:
        """Snapshot live policy and evidence, optionally only in AUTO."""
        context = (
            self.context_provider() if self.context_provider else self.context
        )
        if auto_only and context.mode != PermissionMode.AUTO:
            return None
        authorization = (
            self.authorization_provider()
            if self.authorization_provider
            else {}
        )
        return context.model_copy(deep=True), self._authorization_basis(
            authorization,
        )

    def _authorization_basis(self, context: dict[str, Any]) -> tuple:
        """Snapshot original instructions and any mutable scope or media."""
        messages = context.get("messages", [])
        evidence = retain_authorization_evidence(
            context.get("authorization_evidence", []),
            messages,
        )
        return (
            tuple(
                (
                    item.message_id,
                    item.block_id,
                    item.source,
                    item.text,
                    item.author,
                    item.created_at,
                )
                if type(item) is AuthorizationEvidence
                else deepcopy(item)
                for item in evidence
            ),
            context.get("evidence_complete", True)
            and not has_unreviewable_input(messages),
            deepcopy(context.get("schedule_authorization")),
            frozenset(self.context.working_directories),
            [
                (message.id, deepcopy(block.model_dump()))
                for message in messages
                if message.role in ("user", "system")
                and has_verified_source(message)
                for block in message.content
                if block.type != "text"
            ],
        )

    @staticmethod
    def _review_transcript(
        messages: list["Msg"],
        archive: list[AuthorizationEvidence] | None = None,
    ) -> list[dict[str, Any]]:
        """Preserve roles and visible evidence while excluding reasoning."""
        references = {
            (item.message_id, item.block_id, item.source, item.text): index
            for index, item in enumerate(archive or [])
        }
        transcript = []
        for message in messages:
            content = []
            for block in message.content:
                if block.type == "thinking":
                    continue
                if block.type == "text":
                    entry = block.model_dump(include={"type", "text"})
                elif block.type == "hint":
                    entry = {
                        "type": "hint",
                        "source": block.source,
                        "source_role": None
                        if not has_verified_source(message)
                        else block.source_role,
                        "hint": block.hint
                        if isinstance(block.hint, str)
                        else [
                            item.model_dump(include={"type", "text"})
                            if item.type == "text"
                            else {"type": item.type, "omitted": True}
                            for item in block.hint
                        ],
                    }
                elif block.type == "tool_call":
                    entry = block.model_dump(include={"type", "name", "input"})
                elif block.type == "tool_result":
                    entry = {
                        "type": block.type,
                        "name": block.name,
                        "output": block.output
                        if isinstance(block.output, str)
                        else [
                            item.model_dump(include={"type", "text"})
                            if item.type == "text"
                            else {"type": item.type, "omitted": True}
                            for item in block.output
                        ],
                    }
                else:
                    entry = {"type": block.type, "omitted": True}

                if references:
                    key = authorization_key(message, block)
                    if key is not None and key in references:
                        ref = {
                            "type": "authorization_evidence_ref",
                            "index": references[key],
                        }
                        if block.type == "hint":
                            ref.update(
                                source=block.source,
                                source_role=block.source_role,
                            )
                        if review_json_size(ref) < review_json_size(entry):
                            entry = ref
                content.append(entry)
            if content:
                transcript.append(
                    {
                        "role": "assistant"
                        if not has_verified_source(message)
                        else message.role,
                        "name": message.name,
                        "content": content,
                    },
                )
        return transcript

    async def _check_deny_rules(
        self,
        tool: "ToolBase",
        input_data: dict[str, Any],
        operations: _RuleInputs,
    ) -> PermissionDecision | None:
        """Check if any deny rules match the request.

        Args:
            tool (`ToolBase`):
                The tool instance being called
            input_data (`dict[str, Any]`):
                The tool input data

        Returns:
            `PermissionDecision | None`:
                DENY decision if a rule matches, None otherwise
        """
        rules = self.context.deny_rules.get(tool.name, [])
        for rule in rules:
            if await self._rule_matches(tool, rule, input_data, operations):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Permission to use {tool.name} has been denied",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    async def _check_ask_rules(
        self,
        tool: "ToolBase",
        input_data: dict[str, Any],
        operations: _RuleInputs,
    ) -> PermissionDecision | None:
        """Check if any ask rules match the request.

        Args:
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific checks)
            input_data (`dict[str, Any]`):
                The tool input data

        Returns:
            `PermissionDecision | None`:
                ASK decision if a rule matches, None otherwise
        """
        rules = self.context.ask_rules.get(tool.name, [])
        for rule in rules:
            if await self._rule_matches(tool, rule, input_data, operations):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required for {tool.name}",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    async def _check_allow_rules(
        self,
        tool: "ToolBase",
        input_data: dict[str, Any],
        operations: _RuleInputs,
    ) -> PermissionDecision | None:
        """Check if any allow rules match the request.

        Args:
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific checks)
            input_data (`dict[str, Any]`):
                The tool input data

        Returns:
            `PermissionDecision | None`:
                ALLOW decision if a rule matches, None otherwise
        """
        rules = self.context.allow_rules.get(tool.name, [])
        if not rules:
            return None
        inputs = operations()
        if not inputs:
            return None
        for operation in inputs:
            for rule in rules:
                if await self._rule_matches(tool, rule, operation):
                    break
            else:
                return None
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"Permission granted for {tool.name}",
            updated_input=input_data,
        )

    async def _rule_matches(
        self,
        tool: "ToolBase",
        rule: PermissionRule,
        input_data: dict[str, Any],
        operations: _RuleInputs | None = None,
    ) -> bool:
        """Check if a rule matches the input data.

        The matching strategy depends on the tool type:
        - Bash: Substring matching against the command
        - Write/Read: Glob pattern matching against file paths
        - Other: Generic pattern matching

        Args:
            rule (`PermissionRule`):
                The permission rule to check
            input_data (`dict[str, Any]`):
                The tool input data

        Returns:
            `bool`:
                True if the rule matches, False otherwise

        Raises:
            `TypeError`:
                If ``match_rule`` does not return a bool.
        """
        if rule.behavior == PermissionBehavior.ALLOW:
            if rule.confirmation_target is not None:
                if rule.confirmation_target != tool.confirmation_identity():
                    return False
            elif rule.source == "suggested" and getattr(tool, "is_mcp", False):
                # Old confirmation cards saved their suggestion verbatim.
                # They contain no evidence of the original service.
                return False
        # Empty rule_content matches everything
        if not rule.rule_content:
            return True

        # Try to use tool's match_rule method if available.
        # ``_execute_async_or_sync_func`` keeps backward compatibility
        # with third-party tools that still override match_rule with a
        # sync ``def`` (the framework's signature is now ``async def``),
        # and awaits a coroutine returned through a sync wrapper.
        # Restrictions apply to any operation, while allow coverage is
        # aggregated across all operations by _check_allow_rules.
        inputs = [input_data]
        if rule.behavior != PermissionBehavior.ALLOW:
            inputs = (
                operations()
                if operations is not None
                else tool.get_permission_inputs(input_data)
            )
        for operation in inputs:
            matched = await _execute_async_or_sync_func(
                tool.match_rule,
                rule.rule_content,
                operation,
            )
            # A truthy non-bool (for example a coroutine that was not
            # awaited) would match every input and fail open.
            if not isinstance(matched, bool):
                raise TypeError(
                    f"{type(tool).__name__}.match_rule() must return bool, "
                    f"got {type(matched).__name__}",
                )
            if matched:
                return True
        return False

    async def _generate_suggestions(
        self,
        tool: "ToolBase",
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules from a tool call.

        This method analyzes the tool call and generates broader permission
        suggestions that the user can apply to avoid future confirmations.

        Strategy:
        - For Bash: Extract command prefix (e.g., "npm run" -> "npm run:*")
        - For File operations: Extract directory (e.g.,
         "src/file.py" -> "src/**")
        - For other tools: Generate exact match rule

        Args:
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific
                suggestions)
            tool_input (`dict[str, Any]`):
                The tool input data (used for generating suggestions)

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules (usually 1, max 5 for
                compound commands)
        """

        # Try to use tool's generate_suggestions method if available.
        # ``_execute_async_or_sync_func`` keeps backward compatibility
        # with third-party tools that still override this method with
        # a sync ``def`` (the framework's signature is now ``async def``).
        return await _execute_async_or_sync_func(
            tool.generate_suggestions,
            tool_input,
        )
