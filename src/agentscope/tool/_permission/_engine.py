# -*- coding: utf-8 -*-
"""The permission engine for checking and enforcing permission rules."""
import fnmatch
import os
import json
from pathlib import Path
from typing import Any, List

from ._bash_parser import BashCommandParser
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

        # Initialize bash parser
        self._bash_parser = BashCommandParser()

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a permission rule to the context.

        Args:
            rule: The permission rule to add

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
            tool_call: The tool call block containing tool name and input

        Returns:
            PermissionDecision indicating whether to allow, deny, or ask
        """

        tool_name = tool_call.name
        input_data = json.loads(tool_call.input)

        # 1. Check tool-level deny rules (highest priority)
        deny_decision = self._check_deny_rules(tool_name, input_data)
        if deny_decision:
            return deny_decision

        # 2. Check tool-level ask rules
        ask_decision = self._check_ask_rules(tool_name, input_data)
        if ask_decision:
            # Generate suggestions for ASK decisions
            ask_decision.suggested_rules = self._generate_suggestions(
                tool_call,
            )
            return ask_decision

        # 3. Tool-specific permission checks (dangerous paths, etc.)
        # These are bypass-immune
        tool_decision = self._tool_check_permissions(tool_name, input_data)

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
            )
            return tool_decision

        # 4. Check allow rules
        allow_decision = self._check_allow_rules(tool_name, input_data)
        if allow_decision:
            return allow_decision

        # 5. Mode-specific auto-allow (ACCEPT_EDITS)
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            mode_decision = self._check_accept_edits_mode(
                tool_name,
                input_data,
            )
            if mode_decision:
                return mode_decision

        # 6. Read-only check (for Bash commands)
        if tool_name == "Bash":
            command = input_data.get("command", "")
            if self._bash_parser.is_read_only_command(command):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="Permission granted for read-only command",
                    decision_reason="Read-only command is allowed",
                )

        # 7. BYPASS mode check
        if self.context.mode == PermissionMode.BYPASS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (bypass mode)",
                decision_reason="Bypass mode allows all operations",
            )

        # 8. Default behavior (passthrough → ask)
        default_decision = self._default_decision_ask(tool_name)
        default_decision.suggested_rules = self._generate_suggestions(
            tool_call,
        )
        return default_decision

    def _tool_check_permissions(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Tool-specific permission checks.

        This includes:
        - EXPLORE mode logic (read-only tools only)
        - Dangerous path checks (safety checks) - bypass-immune

        Note: ACCEPT_EDITS mode logic is handled separately after allow rules.

        Args:
            tool_name: The name of the tool
            input_data: The tool input data

        Returns:
            PermissionDecision if tool has specific logic, None for passthrough
        """
        # EXPLORE mode: read-only tools only
        if self.context.mode == PermissionMode.EXPLORE:
            explore_decision = self._check_explore_mode(tool_name)
            if explore_decision:
                return explore_decision

        # Check dangerous paths for file operations and bash commands
        # This is a safety check that is bypass-immune
        dangerous_path_decision = self._check_dangerous_paths(
            tool_name,
            input_data,
        )
        if dangerous_path_decision:
            return dangerous_path_decision

        # No specific tool logic, passthrough
        return None

    def _check_dangerous_paths(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> PermissionDecision | None:
        """Check for dangerous paths in file operations and bash commands.

        This is a safety check that is bypass-immune.
        Uses tree-sitter to extract file paths from bash commands.

        Args:
            tool_name: The name of the tool
            input_data: The tool input data

        Returns:
            ASK decision if dangerous path detected, None otherwise
        """
        # Check Write/Edit tools
        if tool_name in ["Write", "Edit"]:
            file_path = input_data.get("file_path")
            if file_path and self._is_dangerous_path(file_path):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required: {tool_name} operation on "
                    f"sensitive file {file_path}",
                    decision_reason="Safety check: dangerous file or "
                    "directory",
                )

        # Check Bash tool for dangerous paths in commands
        if tool_name == "Bash":
            command = input_data.get("command", "")
            dangerous_paths = self._extract_dangerous_paths_from_bash(command)
            if dangerous_paths:
                paths_str = ", ".join(dangerous_paths)
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    message=f"Permission required: Bash command operates on "
                    f"sensitive paths: {paths_str}",
                    decision_reason="Safety check: dangerous file or "
                    "directory in bash command",
                )

        return None

    def _extract_dangerous_paths_from_bash(
        self,
        command: str,
    ) -> list[str]:
        """Extract dangerous paths from a bash command using tree-sitter.

        Checks for dangerous paths in:
        - File-manipulating commands (rm, mv, cp, chmod, chown, sed, touch)
        - Output redirections (>, >>)

        Args:
            command: The bash command string

        Returns:
            List of dangerous paths found in the command
        """
        dangerous_paths = []

        # Use tree-sitter to extract file paths
        file_paths = self._bash_parser.extract_file_paths(command)

        for _cmd_name, path in file_paths:
            if self._is_dangerous_path(path):
                dangerous_paths.append(path)

        return dangerous_paths

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
        - Dangerous paths are NOT auto-allowed
         (handled by _check_dangerous_paths)
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

            # Dangerous paths are already checked in _check_dangerous_paths
            # Here we only check if in working directory
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

        # Handle Edit tool
        if tool_name == "Edit":
            file_path = input_data.get("file_path")
            if not file_path:
                return None

            # Dangerous paths are already checked in _check_dangerous_paths
            # Here we only check if in working directory
            if self._path_in_allowed_working_path(file_path):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for editing {file_path} "
                    f"(accept edits mode - in working directory)",
                    decision_reason="File is in working directory and not "
                    "a dangerous path",
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

        if rule.tool_name in ["Write", "Read"]:
            return self._match_filesystem_rule(rule.rule_content, input_data)

        return self._match_generic_rule(rule.rule_content, input_data)

    def _match_bash_rule(
        self,
        pattern: str,
        input_data: dict[str, Any],
    ) -> bool:
        """Match Bash command using substring or prefix matching.

        Supports two matching modes:
        1. Prefix pattern (e.g., "git:*"): matches commands starting
         with "git "
        2. Substring pattern (e.g., "npm install"): matches if pattern is
         in command

        Args:
            pattern: The command pattern to match
            input_data: Must contain a "command" key with the command string

        Returns:
            True if pattern matches the command

        Examples:
            pattern="git:*" matches "git status", "git add .", etc.
            pattern="npm install" matches "npm install express"
        """
        command = input_data.get("command", "")

        # Check if pattern is a prefix pattern (ends with :*)
        if pattern.endswith(":*"):
            # Extract prefix (remove :*)
            prefix = pattern[:-2].strip()
            # Match if command starts with prefix followed by space or is
            # exactly the prefix
            return command.startswith(prefix + " ") or command == prefix

        # Otherwise, use substring matching
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

    def _default_decision_ask(self, tool_name: str) -> PermissionDecision:
        """Return default ASK decision.

        Args:
            tool_name: The name of the tool

        Returns:
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

        Generates prefix rules based on command + subcommand (two words).
        For example, "git commit -m 'xxx'" generates "git commit:*".
        """
        input_data = json.loads(tool_call.input)
        command = input_data.get("command", "")
        if not command:
            return []

        # Use bash parser to extract command prefixes
        prefixes = self._bash_parser.extract_command_prefixes(
            command,
            max_prefixes=5,
        )

        if not prefixes:
            # Cannot extract any prefix, return empty
            return []

        # Generate rules for each prefix
        rules = []
        for prefix in prefixes:
            rules.append(
                PermissionRule(
                    tool_name="Bash",
                    rule_content=f"{prefix}:*",
                    behavior=PermissionBehavior.ALLOW,
                    source="suggested",
                ),
            )

        return rules

    def _generate_file_suggestions(
        self,
        tool_call: ToolCallBlock,
    ) -> List[PermissionRule]:
        """Generate suggestions for file operations.

        Suggests allowing the entire directory containing the file.
        """
        input_data = json.loads(tool_call.input)
        file_path = input_data.get("file_path", "")
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
        input_data = json.loads(tool_call.input)
        rule_content = json.dumps(input_data, sort_keys=True)

        return [
            PermissionRule(
                tool_name=tool_call.name,
                rule_content=rule_content,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]
