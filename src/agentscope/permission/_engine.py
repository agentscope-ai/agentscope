# -*- coding: utf-8 -*-
"""The permission engine for checking and enforcing permission rules."""
from typing import Any, List, TYPE_CHECKING

from ._context import PermissionContext
from ._rule import PermissionRule
from ._decision import PermissionDecision, PermissionBehavior
from ._evaluation import PermissionEvaluation, PermissionResolution
from ._types import PermissionMode
from .._utils._common import _execute_async_or_sync_func

if TYPE_CHECKING:
    from ..tool import ToolBase
else:
    ToolBase = "ToolBase"


class PermissionEngine:
    """Engine for checking and enforcing permission rules.

    Evaluates tool execution requests against configured permission rules.
    Matching strategy is delegated to each tool's :meth:`ToolBase.match_rule`:

    - Bash tools: substring / prefix wildcard matching against the command
    - Write/Read/Edit tools: glob matching against file paths
    - Other tools: generic pattern matching (or tool-name-level only)

    Each :class:`PermissionMode` has its own ``_check_<mode>`` method so
    that mode policies are self-contained and readable in isolation. See
    :meth:`check_permission` for the dispatcher and the individual methods
    for per-mode evaluation order.
    """

    def __init__(
        self,
        context: PermissionContext,
    ) -> None:
        """Initialize the permission engine.

        Args:
            context (`PermissionContext`):
                The permission context containing rules and mode

        Example:
            >>> context = PermissionContext(mode=PermissionMode.ACCEPT_EDITS)
            >>> engine = PermissionEngine(context)
        """
        self.context = context

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a permission rule to the context.

        Args:
            rule (`PermissionRule`):
                The permission rule to add

        Example:
            >>> engine.add_rule(PermissionRule(
            ...     tool_name="Bash",
            ...     rule_content="git:*",
            ...     behavior=PermissionBehavior.ALLOW,
            ... ))
        """

        if rule.behavior == PermissionBehavior.ALLOW:
            if rule.tool_name not in self.context.allow_rules:
                self.context.allow_rules[rule.tool_name] = []
            self.context.allow_rules[rule.tool_name].append(rule)
        elif rule.behavior == PermissionBehavior.DENY:
            if rule.tool_name not in self.context.deny_rules:
                self.context.deny_rules[rule.tool_name] = []
            self.context.deny_rules[rule.tool_name].append(rule)
        elif rule.behavior == PermissionBehavior.ASK:
            if rule.tool_name not in self.context.ask_rules:
                self.context.ask_rules[rule.tool_name] = []
            self.context.ask_rules[rule.tool_name].append(rule)

    async def check_permission(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Check permission for a tool execution request.

        Returns the final :class:`PermissionDecision` only. Use
        :meth:`evaluate_permission` when you need the structured
        evaluation (including any candidate decision suppressed by the
        active mode).

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data, used for rule matching and
                tool-specific checks.

        Returns:
            `PermissionDecision`:
                Decision indicating whether to allow, deny, or ask.
        """
        evaluation = await self.evaluate_permission(tool, tool_input)
        return evaluation.effective_decision

    async def evaluate_permission(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Evaluate permission and return a structured result.

        Like :meth:`check_permission` but also exposes any candidate
        decision that was transformed or suppressed by the active mode
        (e.g. a BYPASS-silenced safety ASK). Dispatches to a per-mode
        private method so each mode's policy is self-contained and
        readable in isolation:

        - DEFAULT      → :meth:`_check_default`
        - EXPLORE      → :meth:`_check_explore`
        - ACCEPT_EDITS → :meth:`_check_accept_edits`
        - BYPASS       → :meth:`_check_bypass`
        - DONT_ASK     → :meth:`_check_dont_ask`

        Args:
            tool (`ToolBase`): The tool instance being called.
            tool_input (`dict[str, Any]`): The tool input data.

        Returns:
            `PermissionEvaluation`: The structured evaluation result.
        """
        mode = self.context.mode
        if mode == PermissionMode.DEFAULT:
            return await self._check_default(tool, tool_input)
        if mode == PermissionMode.EXPLORE:
            return await self._check_explore(tool, tool_input)
        if mode == PermissionMode.ACCEPT_EDITS:
            return await self._check_accept_edits(tool, tool_input)
        if mode == PermissionMode.BYPASS:
            return await self._check_bypass(tool, tool_input)
        if mode == PermissionMode.DONT_ASK:
            return await self._check_dont_ask(tool, tool_input)
        raise ValueError(f"Unknown permission mode: {mode}")

    def _direct(
        self,
        decision: PermissionDecision,
    ) -> PermissionEvaluation:
        """Wrap a directly-returned decision (no transformation)."""
        return PermissionEvaluation(
            mode=self.context.mode,
            effective_decision=decision,
        )

    def _resolved(
        self,
        effective: PermissionDecision,
        candidate: PermissionDecision,
        resolution: PermissionResolution,
    ) -> PermissionEvaluation:
        """Wrap a decision that transformed/suppressed a candidate."""
        return PermissionEvaluation(
            mode=self.context.mode,
            effective_decision=effective,
            candidate_decision=candidate,
            resolution=resolution,
        )

    async def _check_default(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Permission check for :attr:`PermissionMode.DEFAULT`.

        Every operation requires explicit permission unless either an
        allow rule matches or the tool's own ``check_permissions``
        explicitly returns ALLOW (e.g. ``Bash`` auto-allows recognized
        read-only commands like ``ls``/``git status``). Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions)
        3. ``tool.check_permissions``:
            - ALLOW / DENY → returned as-is
            - Safety ASK (bypass-immune) → returned with suggestions; cannot
              be overridden by allow rules
            - Non-safety ASK / PASSTHROUGH → continue
        4. Allow rules → ALLOW
        5. Default → ASK (with suggestions)

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionEvaluation`:
                The final evaluation. A tool ASK overridden by an allow
                rule uses ``ASK_OVERRIDDEN_BY_ALLOW_RULE``; other paths
                are direct.
        """
        # step 1: deny rules — highest priority
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return self._direct(deny)

        # step 2: ask rules
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(ask)

        # step 3: tool's own check_permissions
        tool_decision = await tool.check_permissions(tool_input, self.context)
        # step 3a: tool ALLOW / DENY returned as-is
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return self._direct(tool_decision)
        # step 3b: safety ASK is bypass-immune — allow rules can't override
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(tool_decision)

        # step 4: allow rules
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            if tool_decision.behavior == PermissionBehavior.ASK:
                return self._resolved(
                    allow,
                    tool_decision,
                    PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE,
                )
            return self._direct(allow)

        # step 5: default — ASK the user
        default = PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool.name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )
        default.suggested_rules = await self._generate_suggestions(
            tool,
            tool_input,
        )
        return self._direct(default)

    async def _check_explore(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Permission check for :attr:`PermissionMode.EXPLORE`.

        Read-only mode — modifications are categorically denied. Evaluation
        order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions)
        3. :meth:`ToolBase.check_read_only` (input-aware):
            - True  → ALLOW
            - False → DENY

        ``tool.check_permissions`` is not invoked: EXPLORE is fully
        resolved by the read-only verdict, so safety ASK paths (e.g.
        ``rm -rf /``) are subsumed into the broader DENY. Allow rules are
        intentionally not consulted — EXPLORE's read-only guarantee
        cannot be granted away by a user-configured rule.

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionEvaluation`:
                ALLOW for read-only invocations, DENY otherwise (always
                ``resolution=DIRECT`` — no mode transformation).
        """
        # step 1: deny rules
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return self._direct(deny)

        # step 2: ask rules
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(ask)

        # step 3: read-only verdict decides everything (ALLOW or DENY)
        if await tool.check_read_only(tool_input):
            return self._direct(
                PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=(
                        f"Permission granted for {tool.name} "
                        f"(explore mode - read-only invocation)"
                    ),
                    decision_reason="Explore mode allows read-only operations",
                ),
            )
        return self._direct(
            PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"Permission denied for {tool.name} "
                    f"(explore mode is read-only)"
                ),
                decision_reason="Explore mode does not allow modifications",
            ),
        )

    async def _check_accept_edits(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Permission check for :attr:`PermissionMode.ACCEPT_EDITS`.

        Edits within working directories are auto-allowed by each tool's
        own ``check_permissions``; other operations follow the normal
        flow. Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions)
        3. :meth:`ToolBase.check_read_only` → True → ALLOW (fast path)
        4. ``tool.check_permissions``:
            - ALLOW (e.g. ``Write`` to a file in the working directory) /
              DENY → returned as-is
            - Safety ASK (bypass-immune) → returned with suggestions
            - Non-safety ASK / PASSTHROUGH → continue
        5. Allow rules → ALLOW
        6. Default → ASK (with suggestions)

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionEvaluation`:
                The final evaluation. A tool ASK overridden by an allow
                rule uses ``ASK_OVERRIDDEN_BY_ALLOW_RULE``; other paths
                are direct.
        """
        # step 1: deny rules
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return self._direct(deny)

        # step 2: ask rules
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(ask)

        # step 3: read-only fast path — ALLOW without invoking the tool
        if await tool.check_read_only(tool_input):
            return self._direct(
                PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=(
                        f"Permission granted for {tool.name} "
                        f"(accept edits mode - read-only invocation)"
                    ),
                    decision_reason="Accept edits mode allows read-only "
                    "operations",
                ),
            )

        # step 4: tool's own check_permissions (working-directory check
        # for Write/Edit, path-checked auto-allow for Bash, ...)
        tool_decision = await tool.check_permissions(tool_input, self.context)
        # step 4a: tool ALLOW / DENY returned as-is
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return self._direct(tool_decision)
        # step 4b: safety ASK is bypass-immune
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(tool_decision)

        # step 5: allow rules
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            if tool_decision.behavior == PermissionBehavior.ASK:
                return self._resolved(
                    allow,
                    tool_decision,
                    PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE,
                )
            return self._direct(allow)

        # step 6: default — ASK the user
        default = PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool.name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )
        default.suggested_rules = await self._generate_suggestions(
            tool,
            tool_input,
        )
        return self._direct(default)

    async def _check_bypass(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Permission check for :attr:`PermissionMode.BYPASS`.

        BYPASS is the "fully trusted" mode: the user has explicitly
        opted out of safety prompts. All tool-emitted safety ASKs
        (``rm -rf /``, write to ``~/.bashrc``, command-injection
        patterns, dangerous sed, etc.) are **skipped** — only
        user-configured deny / ask rules and tool-emitted DENY remain
        as guardrails. The :attr:`PermissionDecision.bypass_immune`
        field has no effect in BYPASS by design.

        Use BYPASS only for sandboxed / containerized environments or
        when you fully trust the agent's behavior. For unattended
        execution where safety still matters, use
        :attr:`PermissionMode.DONT_ASK` instead — it converts safety
        ASKs to DENY rather than skipping them.

        Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions; honors explicit user intent)
        3. ``tool.check_permissions``:
            - ALLOW / DENY → returned as-is
            - ASK (including bypass-immune safety ASKs) → falls through
            - PASSTHROUGH → falls through
        4. Allow rules → ALLOW
        5. Fallback → ALLOW (BYPASS)

        When a tool-emitted ASK is suppressed (steps 4–5), the original
        ASK is preserved as :attr:`PermissionEvaluation.candidate_decision`
        with :attr:`PermissionResolution.BYPASS_ASK_SUPPRESSED`, so an
        observer can audit the suppression even though the final ALLOW
        no longer carries ``bypass_immune``.

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionEvaluation`:
                The final evaluation. ``resolution=BYPASS_ASK_SUPPRESSED``
                when a tool ASK was silenced into ALLOW.
        """
        # step 1: deny rules
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return self._direct(deny)

        # step 2: ask rules (honor explicit user intent to be prompted)
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._direct(ask)

        # step 3: tool's own check_permissions — ALLOW / DENY returned;
        # any ASK (including bypass-immune safety ASK) is intentionally
        # NOT honored here, per BYPASS's "skip safety prompts" contract.
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return self._direct(tool_decision)

        # step 4: allow rules. A tool-emitted ASK reaching here is being
        # suppressed — record it as the candidate.
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            if tool_decision.behavior == PermissionBehavior.ASK:
                return self._resolved(
                    allow,
                    tool_decision,
                    PermissionResolution.BYPASS_ASK_SUPPRESSED,
                )
            return self._direct(allow)

        # step 5: bypass fallback — ALLOW everything else. Same candidate
        # preservation as step 4 when a tool ASK was suppressed.
        fallback = PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"Permission granted for {tool.name} (bypass mode)",
            decision_reason="Bypass mode allows all operations",
        )
        if tool_decision.behavior == PermissionBehavior.ASK:
            return self._resolved(
                fallback,
                tool_decision,
                PermissionResolution.BYPASS_ASK_SUPPRESSED,
            )
        return self._direct(fallback)

    async def _check_dont_ask(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionEvaluation:
        """Permission check for :attr:`PermissionMode.DONT_ASK`.

        Used when no user is available to answer prompts (scheduled
        tasks, background runs). Invariant: this method must never
        return :attr:`PermissionBehavior.ASK` — every code path that
        would otherwise ASK is converted to DENY via
        :meth:`_convert_ask_to_deny`. Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → DENY (converted, with suggestions preserved)
        3. ``tool.check_permissions``:
            - ALLOW / DENY → returned as-is
            - Safety ASK → DENY (converted, with suggestions preserved)
            - Non-safety ASK / PASSTHROUGH → continue
        4. Allow rules → ALLOW
        5. Default → DENY (user not available to answer)

        Every tool/rule ASK resolved to a non-ASK preserves the original
        decision as :attr:`PermissionEvaluation.candidate_decision`.
        Conversions to DENY use ``ASK_CONVERTED_TO_DENY``; an allow-rule
        override uses ``ASK_OVERRIDDEN_BY_ALLOW_RULE``.

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionEvaluation`:
                The final evaluation (effective decision never ASK).
        """
        # step 1: deny rules
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return self._direct(deny)

        # step 2: ask rules — converted to DENY (no user available)
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._resolved(
                self._convert_ask_to_deny(tool, ask),
                ask,
                PermissionResolution.ASK_CONVERTED_TO_DENY,
            )

        # step 3: tool's own check_permissions
        tool_decision = await tool.check_permissions(tool_input, self.context)
        # step 3a: tool ALLOW / DENY returned as-is
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return self._direct(tool_decision)
        # step 3b: safety ASK converted to DENY (no user available)
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._resolved(
                self._convert_ask_to_deny(tool, tool_decision),
                tool_decision,
                PermissionResolution.ASK_CONVERTED_TO_DENY,
            )

        # step 4: allow rules
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            if tool_decision.behavior == PermissionBehavior.ASK:
                return self._resolved(
                    allow,
                    tool_decision,
                    PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE,
                )
            return self._direct(allow)

        # step 5: default — DENY (no user available to confirm)
        fallback = PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=(
                f"Permission denied for {tool.name} "
                f"(dont_ask mode - user not available)"
            ),
            decision_reason="User is not available to answer permission "
            "prompts",
        )
        if tool_decision.behavior == PermissionBehavior.ASK:
            return self._resolved(
                fallback,
                tool_decision,
                PermissionResolution.ASK_CONVERTED_TO_DENY,
            )
        return self._direct(fallback)

    @staticmethod
    def _convert_ask_to_deny(
        tool: ToolBase,
        ask_decision: PermissionDecision,
    ) -> PermissionDecision:
        """Convert an ASK decision into a DENY for DONT_ASK mode.

        DONT_ASK's invariant is "never return ASK" — the user is not
        available to answer prompts. This helper turns whatever produced
        the ASK (an ASK rule, a safety check) into a DENY while
        preserving traceability by carrying the original reason and
        ``suggested_rules`` forward; callers (e.g. a UI surfacing the
        scheduled-task failure) can still show the user what rule they
        could add to unblock the operation in the future.

        Args:
            tool (`ToolBase`):
                The tool whose invocation is being denied.
            ask_decision (`PermissionDecision`):
                The original ASK decision to convert.

        Returns:
            `PermissionDecision`:
                A DENY decision with the original ASK's reason and
                suggestions attached.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=(
                f"Permission denied for {tool.name} "
                f"(dont_ask mode - ASK converted to DENY, "
                f"user not available)"
            ),
            decision_reason=(
                f"DONT_ASK mode converted ASK to DENY. "
                f"Original reason: {ask_decision.decision_reason}"
            ),
            suggested_rules=ask_decision.suggested_rules,
        )

    @staticmethod
    def _is_safety_ask(decision: PermissionDecision) -> bool:
        """Whether a decision is a bypass-immune safety ASK.

        A safety ASK is an ASK that a tool has explicitly marked with
        :attr:`PermissionDecision.bypass_immune` ``= True``. Tools emit
        these for dangerous operations (e.g. write to ``~/.bashrc``,
        ``rm -rf /``, command-injection patterns) that must be surfaced
        to the user regardless of allow rules in
        ``DEFAULT``/``ACCEPT_EDITS``. ``BYPASS`` mode intentionally
        skips this check (see :meth:`_check_bypass`); ``DONT_ASK``
        converts the ASK to DENY (see :meth:`_check_dont_ask`).

        Args:
            decision (`PermissionDecision`):
                The decision returned by a tool's ``check_permissions``.

        Returns:
            `bool`:
                True iff ``behavior == ASK`` and ``bypass_immune`` is set.
        """
        return (
            decision.behavior == PermissionBehavior.ASK
            and decision.bypass_immune
        )

    async def _check_deny_rules(
        self,
        tool: ToolBase,
        input_data: dict[str, Any],
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
            if await self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Permission to use {tool.name} has been denied",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    async def _check_ask_rules(
        self,
        tool: ToolBase,
        input_data: dict[str, Any],
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
            if await self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required for {tool.name}",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    async def _check_allow_rules(
        self,
        tool: ToolBase,
        input_data: dict[str, Any],
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
        for rule in rules:
            if await self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for {tool.name}",
                    updated_input=input_data,
                )
        return None

    async def _rule_matches(
        self,
        tool: ToolBase,
        rule: PermissionRule,
        input_data: dict[str, Any],
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
        """
        # Empty rule_content matches everything
        if not rule.rule_content:
            return True

        # Try to use tool's match_rule method if available.
        # ``_execute_async_or_sync_func`` keeps backward compatibility
        # with third-party tools that still override match_rule with a
        # sync ``def`` (the framework's signature is now ``async def``).
        return await _execute_async_or_sync_func(
            tool.match_rule,
            rule.rule_content,
            input_data,
        )

    async def _generate_suggestions(
        self,
        tool: ToolBase,
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
