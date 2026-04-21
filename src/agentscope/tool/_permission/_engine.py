# -*- coding: utf-8 -*-
"""The permission engine for checking and enforcing permission rules."""
import json
from typing import Any, List, TYPE_CHECKING

from ._context import PermissionContext
from ._rule import PermissionRule
from ._decision import PermissionDecision, PermissionBehavior
from ._types import PermissionMode
from ...message import ToolCallBlock

if TYPE_CHECKING:
    from .._base import ToolBase
else:
    ToolBase = "ToolBase"


class PermissionEngine:
    """Engine for checking and enforcing permission rules.

    The PermissionEngine evaluates tool execution requests against configured
    permission rules. It supports different matching strategies based on
    tool type:

    - Bash tools: Matches command substrings
    - Write/Read tools: Matches file paths using glob patterns
    - Other tools: Uses generic pattern matching

    The evaluation order is:
    1. Check deny rules (highest priority)
    2. Check mode-specific logic (EXPLORE, ACCEPT_EDITS, dangerous paths)
    3. Check ask rules
    4. Check allow rules
    5. Apply default behavior based on permission mode
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
        tool_call: ToolCallBlock,
        tool: ToolBase,
    ) -> PermissionDecision:
        """Check permission for a tool execution request.

        The evaluation order:
        1. Tool-level deny rules (highest priority)
        2. Tool-level ask rules
        3. Tool-specific checks (dangerous paths, content rules), bypass-immune
        4. Allow rules (exact + content-specific)
        5. Mode-specific logic (ACCEPT_EDITS auto-allow)
        6. Read-only check (for Bash commands)
        7. BYPASS mode check
        8. Default behavior (passthrough → ask)

        Args:
            tool_call (`ToolCallBlock`):
                The tool call block containing tool name and input
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific checks)

        Returns:
            `PermissionDecision`:
                Decision indicating whether to allow, deny, or ask
        """

        tool_name = tool_call.name
        input_data = json.loads(tool_call.input)

        # 1. Check tool-level deny rules (highest priority)
        deny_decision = self._check_deny_rules(tool, input_data)
        if deny_decision:
            return deny_decision

        # 2. Check tool-level ask rules
        ask_decision = self._check_ask_rules(tool, input_data)
        if ask_decision:
            # Generate suggestions for ASK decisions
            ask_decision.suggested_rules = self._generate_suggestions(
                tool_call,
                tool,
            )
            return ask_decision

        # 3. Tool-specific permission checks (dangerous paths, etc.)
        # These are bypass-immune
        tool_decision = await self._tool_check_permissions(
            input_data,
            tool,
        )

        # 3a. Tool denied permission (bypass-immune)
        if tool_decision and tool_decision.behavior == PermissionBehavior.DENY:
            return tool_decision

        # 3b. Tool allowed permission (e.g., EXPLORE mode allows Read)
        if (
            tool_decision
            and tool_decision.behavior == PermissionBehavior.ALLOW
        ):
            return tool_decision

        # 3c. Safety checks (bypass-immune)
        if (
            tool_decision
            and tool_decision.behavior == PermissionBehavior.ASK
            and tool_decision.decision_reason
            and "safety" in tool_decision.decision_reason.lower()
        ):
            tool_decision.suggested_rules = self._generate_suggestions(
                tool_call,
                tool,
            )
            return tool_decision

        # 4. Check allow rules
        allow_decision = self._check_allow_rules(tool, input_data)
        if allow_decision:
            return allow_decision

        # 5. BYPASS mode check
        if self.context.mode == PermissionMode.BYPASS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (bypass mode)",
                decision_reason="Bypass mode allows all operations",
            )

        # 7. Default behavior (passthrough → ask)
        default_decision = self._default_decision_ask(tool_name)
        default_decision.suggested_rules = self._generate_suggestions(
            tool_call,
            tool,
        )
        return default_decision

    async def _tool_check_permissions(
        self,
        input_data: dict[str, Any],
        tool: ToolBase,
    ) -> PermissionDecision | None:
        """Tool-specific permission checks.

        This includes:
        - EXPLORE mode logic (read-only tools only)
        - Tool's own check_permissions() method

        Args:
            input_data (`dict[str, Any]`):
                The tool input data
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific checks)

        Returns:
            `PermissionDecision | None`:
                PermissionDecision if tool has specific logic, None for
                passthrough
        """
        # EXPLORE and ACCEPT_EDITS modes: check read-only tool handling
        if self.context.mode in (
            PermissionMode.EXPLORE,
            PermissionMode.ACCEPT_EDITS,
        ):
            mode_decision = self._check_explore_mode(tool)
            if mode_decision:
                return mode_decision

        # Call tool's own check_permissions method if available
        decision = await tool.check_permissions(
            input_data,
            self.context,
        )
        # If tool returns PASSTHROUGH, continue with Engine's logic
        if decision.behavior != PermissionBehavior.PASSTHROUGH:
            return decision

        # No specific tool logic, passthrough
        return None

    def _check_explore_mode(
        self,
        tool: ToolBase,
    ) -> PermissionDecision | None:
        """Check permissions for read-only tools in EXPLORE and
        ACCEPT_EDITS modes.

        EXPLORE mode: only allows read-only tools, denies modifications
        ACCEPT_EDITS mode: allows read-only tools (no restrictions)

        Args:
            tool (`ToolBase`):
                The tool instance being called (used to check is_read_only)

        Returns:
            `PermissionDecision | None`:
                ALLOW for read-only tools in EXPLORE/ACCEPT_EDITS modes,
                DENY for modification tools in EXPLORE mode,
                None otherwise
        """
        # EXPLORE mode: allow read-only, deny modifications
        if self.context.mode == PermissionMode.EXPLORE:
            if tool.is_read_only:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=(
                        f"Permission granted for {tool.name} "
                        f"(explore mode - read-only tool)"
                    ),
                    decision_reason="Explore mode allows read-only operations",
                )
            else:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=(
                        f"Permission denied for {tool.name} "
                        f"(explore mode is read-only)"
                    ),
                    decision_reason="Explore mode does not allow "
                    "modifications",
                )

        # ACCEPT_EDITS mode: allow read-only tools
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            if tool.is_read_only:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=(
                        f"Permission granted for {tool.name} "
                        f"(accept edits mode - read-only tool)"
                    ),
                    decision_reason="Accept edits mode allows read-only "
                    "operations",
                )

        return None

    def _check_deny_rules(
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
            if self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Permission to use {tool.name} has been denied",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    def _check_ask_rules(
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
            if self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required for {tool.name}",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    def _check_allow_rules(
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
            if self._rule_matches(tool, rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for {tool.name}",
                    updated_input=input_data,
                )
        return None

    def _rule_matches(
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

        # Try to use tool's match_rule method if available
        return tool.match_rule(rule.rule_content, input_data)

    def _default_decision_ask(self, tool_name: str) -> PermissionDecision:
        """Return default ASK decision.

        Args:
            tool_name (`str`):
                The name of the tool

        Returns:
            `PermissionDecision`:
                PermissionDecision with ASK behavior
        """
        # DONT_ASK: Convert ASK to DENY (user not available)
        if self.context.mode == PermissionMode.DONT_ASK:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Permission denied for {tool_name} "
                f"(dont_ask mode - user not available)",
                decision_reason="User is not available to answer "
                "permission prompts",
            )

        # DEFAULT and other modes: Ask for permission
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool_name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )

    def _generate_suggestions(
        self,
        tool_call: ToolCallBlock,
        tool: ToolBase,
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
            tool_call (`ToolCallBlock`):
                The tool call block containing name and parameters
            tool (`ToolBase`):
                The tool instance being called (used for tool-specific
                suggestions)

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules (usually 1, max 5 for
                compound commands)
        """
        input_data = json.loads(tool_call.input)

        # Try to use tool's generate_suggestions method if available
        return tool.generate_suggestions(input_data)
