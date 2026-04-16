# -*- coding: utf-8 -*-
"""The permission engine for checking and enforcing permission rules."""
import fnmatch
import os
from pathlib import Path
from typing import Any, List

from ._bash_parser import get_bash_parser
from ._context import PermissionContext
from ._rule import PermissionRule
from ._decision import PermissionDecision, PermissionBehavior
from ._types import PermissionMode
from ...message import ToolCallBlock

# ============================================================================
# Built-in Dangerous Paths
# ============================================================================

DEFAULT_DANGEROUS_FILES = [
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ssh/config",
    ".ssh/authorized_keys",
    ".netrc",
    ".npmrc",
    ".pypirc",
]
# Built-in list of dangerous files that should be protected from auto-editing.
#
# These files can be used for code execution, credential storage, or data
# exfiltration:
# - Shell configuration files: .bashrc, .zshrc, .profile, etc.
# - Git configuration: .gitconfig, .gitmodules
# - SSH configuration: .ssh/config, .ssh/authorized_keys
# - Credential files: .netrc, .npmrc, .pypirc


DEFAULT_DANGEROUS_DIRECTORIES = [
    ".git",
    ".vscode",
    ".idea",
    ".ssh",
]
# Built-in list of dangerous directories that should be protected from
# auto-editing.
#
# These directories contain sensitive configuration or executable files:
# - .git: Git repository metadata
# - .vscode: VS Code configuration
# - .idea: JetBrains IDE configuration
# - .ssh: SSH keys and configuration


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
        additional_dangerous_files: list[str] | None = None,
        additional_dangerous_directories: list[str] | None = None,
    ):
        """Initialize the permission engine.

        Args:
            context: The permission context containing rules and mode
            additional_dangerous_files: Additional dangerous files to check
                (added to built-in defaults). Use this to add project-specific
                sensitive files like '.env' or '.secrets'.
            additional_dangerous_directories: Additional dangerous directories
                to check (added to built-in defaults). Use this to add
                project-specific sensitive directories.

        Example:
            >>> context = PermissionContext(mode=PermissionMode.ACCEPT_EDITS)
            >>> engine = PermissionEngine(
            ...     context,
            ...     additional_dangerous_files=['.env', '.secrets'],
            ...     additional_dangerous_directories=['secrets/']
            ... )
        """
        self.context = context

        # Merge built-in and additional dangerous paths
        self.dangerous_files = DEFAULT_DANGEROUS_FILES.copy()
        if additional_dangerous_files:
            self.dangerous_files.extend(additional_dangerous_files)

        self.dangerous_directories = DEFAULT_DANGEROUS_DIRECTORIES.copy()
        if additional_dangerous_directories:
            self.dangerous_directories.extend(additional_dangerous_directories)

        # Initialize bash parser (lazy loading)
        self._bash_parser = None

    async def check_permission(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        tool_call: ToolCallBlock | None = None,
    ) -> PermissionDecision:
        """Check permission for a tool execution request.

        The evaluation order is:
        1. Check deny rules (highest priority)
        2. Check mode-specific logic (EXPLORE, ACCEPT_EDITS, etc.)
        3. Check ask rules
        4. Check allow rules
        5. Apply default behavior based on permission mode

        Args:
            tool_name: The name of the tool to execute
            input_data: The input parameters for the tool
            tool_call: Optional ToolCallBlock for generating suggestions

        Returns:
            PermissionDecision indicating whether to allow, deny, or ask
        """
        # 1. Check deny rules (highest priority)
        deny_decision = self._check_deny_rules(tool_name, input_data)
        if deny_decision:
            return deny_decision

        # 2. Check mode-specific logic
        mode_decision = self._check_mode_specific(tool_name, input_data)
        if mode_decision:
            return mode_decision

        # 3. Check ask rules
        ask_decision = self._check_ask_rules(tool_name, input_data)
        if ask_decision:
            # Generate suggestions for ASK decisions
            if tool_call:
                ask_decision.suggested_rules = self._generate_suggestions(
                    tool_call,
                )
            return ask_decision

        # 4. Check allow rules
        allow_decision = self._check_allow_rules(tool_name, input_data)
        if allow_decision:
            return allow_decision

        # 5. Apply default behavior based on mode
        default_decision = self._default_decision(tool_name)
        # Generate suggestions for default ASK decisions
        if default_decision.behavior == PermissionBehavior.ASK and tool_call:
            default_decision.suggested_rules = self._generate_suggestions(
                tool_call,
            )
        return default_decision

    def _check_mode_specific(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check mode-specific permission logic.

        Different modes have special handling:
        - EXPLORE: Only allow read-only tools (Read, Grep, Glob)
        - ACCEPT_EDITS: Auto-allow file operations in working directories
        - BYPASS: Handled in _default_decision
        - DONT_ASK: Handled in _default_decision
        - DEFAULT: No special handling

        Args:
            tool_name: The name of the tool
            input_data: The tool input data

        Returns:
            PermissionDecision if mode-specific logic applies, None otherwise
        """
        # EXPLORE mode: read-only
        if self.context.mode == PermissionMode.EXPLORE:
            return self._check_explore_mode(tool_name)

        # ACCEPT_EDITS mode: auto-allow file operations in working directories
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            return self._check_accept_edits_mode(tool_name, input_data)

        return None

    def _check_explore_mode(self, tool_name: str) -> PermissionDecision | None:
        """Check permissions in EXPLORE (read-only) mode.

        Args:
            tool_name: The name of the tool

        Returns:
            ALLOW for read-only tools, DENY for modification tools
        """
        # Read-only tools are allowed
        read_only_tools = ["Read", "Grep", "Glob"]
        if tool_name in read_only_tools:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=(
                    f"Permission granted for {tool_name} (explore mode - "
                    f"read-only tool)"
                ),
                decision_reason="Explore mode allows read-only operations",
            )

        # Modification tools are denied
        modification_tools = ["Write", "Edit", "Bash", "PowerShell"]
        if tool_name in modification_tools:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"Permission denied for {tool_name} (explore mode is "
                    f"read-only)"
                ),
                decision_reason="Explore mode does not allow modifications",
            )

        # Other tools: no special handling
        return None

    def _check_accept_edits_mode(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check permissions in ACCEPT_EDITS mode.

        In ACCEPT_EDITS mode:
        - Dangerous paths are NOT auto-allowed (require explicit permission)
        - File writes in working directories are auto-allowed
        - File reads in working directories are auto-allowed
        - Common filesystem commands (mkdir, rm, mv, cp) are auto-allowed

        Args:
            tool_name: The name of the tool
            input_data: The tool input data

        Returns:
            ALLOW if operation is safe and in working directory, None otherwise
        """
        # Handle Write tool
        if tool_name == "Write":
            file_path = input_data.get("file_path")
            if not file_path:
                return None

            # 1. Check if path is dangerous (highest priority)
            if self._is_dangerous_path(file_path):
                # Don't auto-allow dangerous paths, let it fall through to ASK
                return None

            # 2. Check if in working directory
            if self._path_in_allowed_working_path(file_path):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for writing {file_path} "
                    f"(accept edits mode - in working directory)",
                    decision_reason="File is in working directory and not "
                    "a dangerous path",
                    updated_input=input_data,
                )

        # Handle Read tool
        if tool_name == "Read":
            file_path = input_data.get("file_path")
            if file_path and self._path_in_allowed_working_path(file_path):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for reading {file_path} "
                    f"(accept edits mode - in working directory)",
                    decision_reason="File is in working directory",
                    updated_input=input_data,
                )

        # Handle Bash tool - auto-allow common filesystem commands
        if tool_name == "Bash":
            command = input_data.get("command", "")
            filesystem_commands = [
                "mkdir",
                "touch",
                "rm",
                "rmdir",
                "mv",
                "cp",
                "sed",
            ]
            base_command = (
                command.strip().split()[0] if command.strip() else ""
            )

            if base_command in filesystem_commands:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for '{base_command}' "
                    f"command (accept edits mode - filesystem command)",
                    decision_reason=f"Filesystem command '{base_command}' "
                    f"is auto-allowed in accept edits mode",
                    updated_input=input_data,
                )

        return None

    def _is_dangerous_path(self, file_path: str) -> bool:
        """Check if a file path is dangerous (sensitive file or directory).

        A path is considered dangerous if:
        1. The filename matches a dangerous file (e.g., .bashrc, .gitconfig)
        2. Any path segment matches a dangerous directory (e.g., .git, .ssh)

        Case-insensitive matching is used to prevent bypasses on
        case-insensitive filesystems (macOS, Windows).

        Args:
            file_path (`file_path`):
                The file path to check

        Returns:
            `bool`:
                True if the path is dangerous and should require explicit
                permission

        Example:
            >>> self._is_dangerous_path("/home/user/.bashrc")
            True
            >>> self._is_dangerous_path("/home/user/.git/config")
            True
            >>> self._is_dangerous_path("/home/user/project/main.py")
            False
        """
        # Normalize path
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Split path into segments
        path_parts = Path(abs_path).parts
        path_parts_lower = [p.lower() for p in path_parts]

        # Check if filename matches dangerous files (case-insensitive)
        filename = os.path.basename(abs_path)
        filename_lower = filename.lower()
        for dangerous_file in self.dangerous_files:
            if filename_lower == dangerous_file.lower():
                return True

        # Check if any path segment matches dangerous directories
        # (case-insensitive)
        for dangerous_dir in self.dangerous_directories:
            dangerous_dir_lower = dangerous_dir.lower()
            if dangerous_dir_lower in path_parts_lower:
                return True

        return False

    def _path_in_allowed_working_path(self, file_path: str) -> bool:
        """Check if a file path is within any allowed working directory.

        Args:
            file_path: The file path to check

        Returns:
            True if the path is within any allowed working directory
        """
        # Get all working directories (current directory + additional)
        all_working_dirs = self._get_all_working_directories()

        # Check if file path is in any working directory
        for working_dir in all_working_dirs:
            if self._path_in_working_path(file_path, working_dir):
                return True

        return False

    def _get_all_working_directories(self) -> list[str]:
        """Get all allowed working directories.

        Returns:
            List of absolute directory paths
        """
        # Current working directory (always included)
        current_dir = os.getcwd()

        # Additional working directories
        additional_dirs = list(self.context.working_directories.keys())

        return [current_dir] + additional_dirs

    def _path_in_working_path(self, file_path: str, working_dir: str) -> bool:
        """Check if a file path is within a specific working directory.

        Args:
            file_path: The file path to check
            working_dir: The working directory path

        Returns:
            True if file_path is inside working_dir
        """

        # Convert to absolute paths
        abs_file_path = os.path.abspath(file_path)
        abs_working_dir = os.path.abspath(working_dir)

        # Calculate relative path
        try:
            rel_path = os.path.relpath(abs_file_path, abs_working_dir)
        except ValueError:
            # Different drives on Windows
            return False

        # Check if path traversal is present (..)
        if rel_path.startswith(".."):
            return False

        # Check if it's an absolute path (means not in working directory)
        if os.path.isabs(rel_path):
            return False

        return True

    def _check_deny_rules(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check if any deny rules match the request."""
        rules = self.context.deny_rules.get(tool_name, [])
        for rule in rules:
            if self._rule_matches(rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Permission to use {tool_name} has been denied",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    def _check_ask_rules(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check if any ask rules match the request."""
        rules = self.context.ask_rules.get(tool_name, [])
        for rule in rules:
            if self._rule_matches(rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required for {tool_name}",
                    decision_reason=f"Rule: {rule.rule_content}",
                )
        return None

    def _check_allow_rules(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check if any allow rules match the request."""
        rules = self.context.allow_rules.get(tool_name, [])
        for rule in rules:
            if self._rule_matches(rule, input_data):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for {tool_name}",
                    updated_input=input_data,
                )
        return None

    def _rule_matches(
        self,
        rule: PermissionRule,
        input_data: dict[str, Any],
    ) -> bool:
        """Check if a rule matches the input data.

        The matching strategy depends on the tool type:
        - Bash: Substring matching against the command
        - Write/Read: Glob pattern matching against file paths
        - Other: Generic pattern matching

        Args:
            rule: The permission rule to check
            input_data: The tool input data

        Returns:
            True if the rule matches, False otherwise
        """
        # Empty rule_content matches everything
        if not rule.rule_content:
            return True

        # Route to appropriate matching logic based on tool type
        if rule.tool_name == "Bash":
            return self._match_bash_rule(rule.rule_content, input_data)
        elif rule.tool_name in ["Write", "Read"]:
            return self._match_filesystem_rule(rule.rule_content, input_data)
        else:
            return self._match_generic_rule(rule.rule_content, input_data)

    def _match_bash_rule(
        self,
        pattern: str,
        input_data: dict[str, Any],
    ) -> bool:
        """Match Bash command using substring matching.

        Args:
            pattern: The command substring pattern to match
            input_data: Must contain a "command" key with the command string

        Returns:
            True if pattern is found in the command

        Example:
            pattern="npm install" matches command="npm install express"
        """
        command = input_data.get("command", "")
        return pattern in command

    def _match_filesystem_rule(
        self,
        pattern: str,
        input_data: dict[str, Any],
    ) -> bool:
        """Match file path using glob pattern matching.

        Args:
            pattern: The glob pattern to match (e.g., "src/**", "*.py")
            input_data: Must contain a "file_path" key with the file path

        Returns:
            True if the file path matches the glob pattern

        Example:
            pattern="src/**" matches file_path="src/main.py"
            pattern="**/.bashrc" matches file_path="/home/user/.bashrc"
        """
        file_path = input_data.get("file_path", "")
        if not file_path:
            return False

        # Try both fnmatch and pathlib matching for compatibility
        try:
            # fnmatch for simple patterns
            if fnmatch.fnmatch(file_path, pattern):
                return True

            # pathlib for more complex glob patterns
            path_obj = Path(file_path)
            if path_obj.match(pattern):
                return True
        except (ValueError, Exception):
            # If pattern is invalid, fall back to substring matching
            return pattern in file_path

        return False

    def _match_generic_rule(
        self,
        pattern: str,
        input_data: dict[str, Any],
    ) -> bool:
        """Generic pattern matching for other tools.

        Performs substring matching against all string values in input_data.

        Args:
            pattern: The pattern to match
            input_data: The tool input data

        Returns:
            True if pattern is found in any string value
        """
        for value in input_data.values():
            if isinstance(value, str) and pattern in value:
                return True
        return False

    def _default_decision(self, tool_name: str) -> PermissionDecision:
        """Determine the default permission behavior based on mode.

        Args:
            tool_name: The name of the tool

        Returns:
            PermissionDecision with the default behavior for the current mode
        """
        # BYPASS: Allow all operations
        if self.context.mode == PermissionMode.BYPASS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (bypass mode)",
            )

        # DONT_ASK: Convert ASK to DENY (user not available)
        if self.context.mode == PermissionMode.DONT_ASK:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Permission denied for {tool_name} "
                f"(dont_ask mode - user not available)",
                decision_reason="User is not available to answer "
                "permission prompts",
            )

        # EXPLORE: Already handled in _check_mode_specific, but as fallback
        if self.context.mode == PermissionMode.EXPLORE:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required for {tool_name}",
                decision_reason=f"Mode: {self.context.mode.value}",
            )

        # DEFAULT and ACCEPT_EDITS: Ask for permission
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Permission required for {tool_name}",
            decision_reason=f"Mode: {self.context.mode.value}",
        )

    def _get_bash_parser(self) -> Any:
        """Get or create bash parser instance (lazy loading)."""
        if self._bash_parser is None:
            self._bash_parser = get_bash_parser()
        return self._bash_parser

    def _generate_suggestions(
        self,
        tool_call: ToolCallBlock,
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
            tool_call: The tool call block containing name and parameters

        Returns:
            List of suggested permission rules (usually 1, max 5 for compound
            commands)
        """
        tool_name = tool_call.name

        if tool_name == "Bash":
            return self._generate_bash_suggestions(tool_call)

        if tool_name in ["Read", "Write", "Edit"]:
            return self._generate_file_suggestions(tool_call)

        return self._generate_exact_suggestions(tool_call)

    def _generate_bash_suggestions(
        self,
        tool_call: "ToolCallBlock",
    ) -> List[PermissionRule]:
        """Generate suggestions for Bash commands.

        For single commands: generates 1 prefix rule
        For compound commands: generates multiple rules (max 5)
        """
        command = tool_call.parameters.get("command", "")
        if not command:
            return []

        parser = self._get_bash_parser()

        # Split compound commands
        subcommands = parser.split_compound_command(command)

        if len(subcommands) == 1:
            # Single command: generate one rule
            prefix = parser.extract_command_prefix(command)
            if prefix:
                return [
                    PermissionRule(
                        tool_name="Bash",
                        rule_content=f"{prefix}:*",
                        behavior=PermissionBehavior.ALLOW,
                        source="suggested",
                    ),
                ]
            else:
                # Cannot extract prefix, use exact match
                return [
                    PermissionRule(
                        tool_name="Bash",
                        rule_content=command,
                        behavior=PermissionBehavior.ALLOW,
                        source="suggested",
                    ),
                ]
        else:
            # Compound command: generate rules for each subcommand (max 5)
            MAX_SUGGESTED_RULES = 5
            rules = []
            seen_contents = set()

            for subcmd in subcommands[:MAX_SUGGESTED_RULES]:
                prefix = parser.extract_command_prefix(subcmd.strip())
                if prefix:
                    rule_content = f"{prefix}:*"
                    # Avoid duplicate rules
                    if rule_content not in seen_contents:
                        rules.append(
                            PermissionRule(
                                tool_name="Bash",
                                rule_content=rule_content,
                                behavior=PermissionBehavior.ALLOW,
                                source="suggested",
                            ),
                        )
                        seen_contents.add(rule_content)

            return rules

    def _generate_file_suggestions(
        self,
        tool_call: ToolCallBlock,
    ) -> List[PermissionRule]:
        """Generate suggestions for file operations.

        Suggests allowing the entire directory containing the file.
        """
        file_path = tool_call.parameters.get("file_path", "")
        if not file_path:
            return []

        # Extract directory
        directory = os.path.dirname(file_path)
        if directory:
            return [
                PermissionRule(
                    tool_name=tool_call.name,
                    rule_content=f"{directory}/**",
                    behavior=PermissionBehavior.ALLOW,
                    source="suggested",
                ),
            ]

        return []

    def _generate_exact_suggestions(
        self,
        tool_call: ToolCallBlock,
    ) -> List[PermissionRule]:
        """Generate exact match rule (fallback strategy).

        Used when no specific suggestion strategy is available.
        """
        import json

        rule_content = json.dumps(tool_call.parameters, sort_keys=True)

        return [
            PermissionRule(
                tool_name=tool_call.name,
                rule_content=rule_content,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]
