# -*- coding: utf-8 -*-
"""The permission engine for checking and enforcing permission rules."""
from typing import Any, List, TYPE_CHECKING

from ._context import PermissionContext
from ._rule import PermissionRule
from ._decision import PermissionDecision, PermissionBehavior
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

        Dispatches to a per-mode private method so each mode's policy
        is self-contained and readable in isolation:

        - DEFAULT      → :meth:`_check_default`
        - EXPLORE      → :meth:`_check_explore`
        - ACCEPT_EDITS → :meth:`_check_accept_edits`
        - BYPASS       → :meth:`_check_bypass`
        - DONT_ASK     → :meth:`_check_dont_ask`

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

    async def _check_default(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Permission check for :attr:`PermissionMode.DEFAULT`.

        Every operation requires explicit permission unless it is a
        read-only invocation, an allow rule matches, or the tool's own
        ``check_permissions`` returns ALLOW. Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions)
        3. Read-only fast path → ALLOW
        4. ``tool.check_permissions``:
            - ALLOW / DENY → returned as-is
            - Safety ASK (bypass-immune) → returned with suggestions; cannot
              be overridden by allow rules
            - Non-safety ASK / PASSTHROUGH → continue
        5. Allow rules → ALLOW
        6. Default → ASK (with suggestions)

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionDecision`:
                The final decision.
        """
        # ================================================================
        # Step 1: Deny rules — highest priority (all modes)
        # ================================================================
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return deny

        # ================================================================
        # Step 2: Ask rules → ASK (with suggestions)
        # ================================================================
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return ask

        # ================================================================
        # Step 3: Read-only fast path → ALLOW
        # ================================================================
        read_only = await self._check_read_only_fast_path(tool, tool_input)
        if read_only:
            return read_only

        # ================================================================
        # Step 4: Tool's own check_permissions
        #   - ALLOW / DENY → returned as-is
        #   - safety ASK (bypass-immune) → returned; allow rules can't override
        #   - non-safety ASK / PASSTHROUGH → continue
        # ================================================================
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return tool_decision
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return tool_decision

        # ================================================================
        # Step 5: Allow rules → ALLOW
        # ================================================================
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            return allow

        # ================================================================
        # Step 6: Mode fallback → ASK the user
        # ================================================================
        default = PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool.name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )
        default.suggested_rules = await self._generate_suggestions(
            tool,
            tool_input,
        )
        return default

    async def _check_explore(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Permission check for :attr:`PermissionMode.EXPLORE`.

        Read-only mode — modifications are categorically denied. Evaluation
        order:

        1. Deny rules → DENY
        2. Ask rules → ASK (with suggestions)
        3. Read-only fast path → ALLOW
        4. ``tool.check_permissions`` → not applicable (see below)
        5. Allow rules → not applicable (see below)
        6. Mode fallback → DENY

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
            `PermissionDecision`:
                ALLOW for read-only invocations, DENY otherwise.
        """
        # ================================================================
        # Step 1: Deny rules — highest priority (all modes)
        # ================================================================
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return deny

        # ================================================================
        # Step 2: Ask rules → ASK (with suggestions)
        # ================================================================
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return ask

        # ================================================================
        # Step 3: Read-only fast path → ALLOW
        # ================================================================
        read_only = await self._check_read_only_fast_path(tool, tool_input)
        if read_only:
            return read_only

        # ================================================================
        # Step 4: Tool's own check_permissions
        #   Not applicable in EXPLORE: any non-read-only op is denied below
        #   regardless of the tool's opinion, so the tool is never consulted
        #   (a safety ASK would only be subsumed into the broader DENY).
        # ================================================================

        # ================================================================
        # Step 5: Allow rules
        #   Not applicable in EXPLORE: the read-only guarantee cannot be
        #   granted away by a user-configured allow rule.
        # ================================================================

        # ================================================================
        # Step 6: Mode fallback → DENY (invocation is not read-only)
        # ================================================================
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=(
                f"Permission denied for {tool.name} "
                f"(explore mode is read-only)"
            ),
            decision_reason="Explore mode does not allow modifications",
        )

    async def _check_accept_edits(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
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
            `PermissionDecision`:
                The final decision.
        """
        # ================================================================
        # Step 1: Deny rules — highest priority (all modes)
        # ================================================================
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return deny

        # ================================================================
        # Step 2: Ask rules → ASK (with suggestions)
        # ================================================================
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return ask

        # ================================================================
        # Step 3: Read-only fast path → ALLOW
        # ================================================================
        read_only = await self._check_read_only_fast_path(tool, tool_input)
        if read_only:
            return read_only

        # ================================================================
        # Step 4: Tool's own check_permissions (working-directory auto-allow
        #   for Write/Edit, path-checked auto-allow for Bash, ...)
        #   - ALLOW / DENY → returned as-is
        #   - safety ASK (bypass-immune) → returned; allow rules can't override
        #   - non-safety ASK / PASSTHROUGH → continue
        # ================================================================
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return tool_decision
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return tool_decision

        # ================================================================
        # Step 5: Allow rules → ALLOW
        # ================================================================
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            return allow

        # ================================================================
        # Step 6: Mode fallback → ASK the user
        # ================================================================
        default = PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool.name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )
        default.suggested_rules = await self._generate_suggestions(
            tool,
            tool_input,
        )
        return default

    async def _check_bypass(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
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
        3. Read-only fast path → ALLOW
        4. ``tool.check_permissions``:
            - ALLOW / DENY → returned as-is
            - ASK (including bypass-immune safety ASKs) → falls through
            - PASSTHROUGH → falls through
        5. Allow rules → ALLOW
        6. Fallback → ALLOW (BYPASS)

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionDecision`:
                The final decision.
        """
        # ================================================================
        # Step 1: Deny rules — highest priority (all modes)
        # ================================================================
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return deny

        # ================================================================
        # Step 2: Ask rules → ASK (honor explicit user intent to be prompted)
        # ================================================================
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return ask

        # ================================================================
        # Step 3: Read-only fast path → ALLOW
        # ================================================================
        read_only = await self._check_read_only_fast_path(tool, tool_input)
        if read_only:
            return read_only

        # ================================================================
        # Step 4: Tool's own check_permissions — ALLOW / DENY returned;
        #   any ASK (including bypass-immune safety ASK) is intentionally
        #   NOT honored here, per BYPASS's "skip safety prompts" contract.
        # ================================================================
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return tool_decision

        # ================================================================
        # Step 5: Allow rules → ALLOW
        # ================================================================
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            return allow

        # ================================================================
        # Step 6: Mode fallback → ALLOW everything else (BYPASS)
        # ================================================================
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"Permission granted for {tool.name} (bypass mode)",
            decision_reason="Bypass mode allows all operations",
        )

    async def _check_dont_ask(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Permission check for :attr:`PermissionMode.DONT_ASK`.

        Used when no user is available to answer prompts (scheduled
        tasks, background runs). Behaves as the *unattended* counterpart
        of :attr:`PermissionMode.ACCEPT_EDITS`: read-only invocations and
        edits within a working directory are auto-allowed, while anything
        that would otherwise prompt the (absent) user is refused.
        Invariant: this method must never return
        :attr:`PermissionBehavior.ASK` — every code path that would
        otherwise ASK is converted to DENY via
        :meth:`_convert_ask_to_deny`. Evaluation order:

        1. Deny rules → DENY
        2. Ask rules → DENY (converted, with suggestions preserved)
        3. Read-only fast path → ALLOW
        4. ``tool.check_permissions``:
            - ALLOW (e.g. an edit within a working directory) / DENY →
              returned as-is
            - Safety ASK → DENY (converted, with suggestions preserved)
            - Non-safety ASK / PASSTHROUGH → continue
        5. Allow rules → ALLOW
        6. Default → DENY (user not available to answer)

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionDecision`:
                The final decision (never ASK).
        """
        # ================================================================
        # Step 1: Deny rules — highest priority (all modes)
        # ================================================================
        deny = await self._check_deny_rules(tool, tool_input)
        if deny:
            return deny

        # ================================================================
        # Step 2: Ask rules → DENY (converted; no user available to answer)
        # ================================================================
        ask = await self._check_ask_rules(tool, tool_input)
        if ask:
            ask.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._convert_ask_to_deny(tool, ask)

        # ================================================================
        # Step 3: Read-only fast path → ALLOW
        # ================================================================
        read_only = await self._check_read_only_fast_path(tool, tool_input)
        if read_only:
            return read_only

        # ================================================================
        # Step 4: Tool's own check_permissions
        #   - ALLOW (e.g. edit within a working directory) / DENY → as-is
        #   - safety ASK → DENY (converted; no user available)
        #   - non-safety ASK / PASSTHROUGH → continue
        # ================================================================
        tool_decision = await tool.check_permissions(tool_input, self.context)
        if tool_decision.behavior in (
            PermissionBehavior.ALLOW,
            PermissionBehavior.DENY,
        ):
            return tool_decision
        if self._is_safety_ask(tool_decision):
            tool_decision.suggested_rules = await self._generate_suggestions(
                tool,
                tool_input,
            )
            return self._convert_ask_to_deny(tool, tool_decision)

        # ================================================================
        # Step 5: Allow rules → ALLOW
        # ================================================================
        allow = await self._check_allow_rules(tool, tool_input)
        if allow:
            return allow

        # ================================================================
        # Step 6: Mode fallback → DENY (no user available to confirm)
        # ================================================================
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=(
                f"Permission denied for {tool.name} "
                f"(dont_ask mode - user not available)"
            ),
            decision_reason="User is not available to answer permission "
            "prompts",
        )

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

    async def _check_read_only_fast_path(
        self,
        tool: ToolBase,
        tool_input: dict[str, Any],
    ) -> PermissionDecision | None:
        """Read-only fast path shared by every mode.

        A read-only invocation has no side effects, so it is auto-allowed
        in every :class:`PermissionMode`. Keeping this in a single helper
        (rather than re-inlining it per mode) guarantees the modes cannot
        drift apart on read-only handling — the exact divergence that
        previously left ``DEFAULT`` and ``DONT_ASK`` without a read-only
        fast path while ``ACCEPT_EDITS`` / ``EXPLORE`` had one.

        Args:
            tool (`ToolBase`):
                The tool instance being called.
            tool_input (`dict[str, Any]`):
                The tool input data.

        Returns:
            `PermissionDecision | None`:
                An ALLOW decision if this invocation is read-only, else None.
        """
        if await tool.check_read_only(tool_input):
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=(
                    f"Permission granted for {tool.name} "
                    f"(read-only invocation)"
                ),
                decision_reason="Read-only operations are auto-allowed",
            )
        return None

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
