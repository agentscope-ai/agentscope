# -*- coding: utf-8 -*-
"""The bash tool in agentscope."""
from typing import AsyncGenerator, Any, List, TYPE_CHECKING

from ._bash_parser import BashCommandParser
from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
)
from .._response import ToolChunk

if TYPE_CHECKING:
    from .._permission import PermissionRule


class Bash(ToolBase):
    """The bash tool."""

    name: str = "Bash"
    """The tool name presented to the agent."""

    description: str = """Executes a bash command and returns its output.

The working directory persists between commands, but shell state does
not. The shell environment is initialized from the user's profile
(bash or zsh).

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`,
`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed
or after you have verified that a dedicated tool cannot accomplish your
task. Instead, use the appropriate dedicated tool as this will provide
a much better experience for the user:

 - File search: Use Glob (NOT find or ls)
 - Content search: Use Grep (NOT grep or rg)
 - Read files: Use Read (NOT cat/head/tail)
 - Edit files: Use Edit (NOT sed/awk)
 - Write files: Use Write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)

While the Bash tool can do similar things, it's better to use the
built-in tools as they provide a better user experience and make it
easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use
   this tool to run `ls` to verify the parent directory exists and is
   the correct location.
 - Always quote file paths that contain spaces with double quotes in
   your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the
   session by using absolute paths and avoiding usage of `cd`. You may
   use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms
   / 10 minutes). By default, your command will timeout after 120000ms
   (2 minutes).
 - Write a clear, concise description of what your command does. For
   simple commands, keep it brief (5-10 words). For complex commands
   (piped commands, obscure flags, or anything hard to understand at a
   glance), include enough context so that the user can understand what
   your command will do.
 - When issuing multiple commands:
  - If the commands are independent and can run in parallel, make
    multiple Bash tool calls in a single message. Example: if you need
    to run "git status" and "git diff", send a single message with two
    Bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially,
    use a single Bash call with '&&' to chain them together.
  - Use ';' only when you need to run commands sequentially but don't
    care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in
    quoted strings).
 - For git commands:
  - Prefer to create a new commit rather than amending an existing
    commit.
  - Before running destructive operations (e.g., git reset --hard, git
    push --force, git checkout --), consider whether there is a safer
    alternative that achieves the same goal. Only use destructive
    operations when they are truly the best approach.
  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign,
    -c commit.gpgsign=false) unless the user has explicitly asked for
    it. If a hook fails, investigate and fix the underlying issue.
 - Avoid unnecessary `sleep` commands:
  - Do not sleep between commands that can run immediately — just run
    them.
  - Do not retry failing commands in a sleep loop — diagnose the root
    cause or consider an alternative approach.
  - If you must sleep, keep the duration short (1-5 seconds) to avoid
    blocking the user."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command "
                    "does. For simple commands, keep it brief (5-10 "
                    "words). For complex commands, include enough "
                    "context."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Optional timeout in milliseconds "
                    "(default: 120000, max: 600000)"
                ),
                "default": 120000,
                "maximum": 600000,
                "minimum": 0,
            },
        },
        "required": ["command"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    def __init__(
        self,
        additional_dangerous_files: list[str] | None = None,
        additional_dangerous_directories: list[str] | None = None,
    ) -> None:
        """Initialize the bash tool.

        Args:
            additional_dangerous_files (`list[str] | None`, optional):
                Additional dangerous files to check (added to built-in
                defaults). Use this to add project-specific sensitive files
                like '.env' or '.secrets'.
            additional_dangerous_directories (`list[str] | None`, optional):
                Additional dangerous directories to check (added to built-in
                defaults). Use this to add project-specific sensitive
                directories.
        """

        self._bash_parser = BashCommandParser()

        # Merge class-level dangerous paths with additional ones
        self.dangerous_files = self.__class__.dangerous_files.copy()
        if additional_dangerous_files:
            self.dangerous_files.extend(additional_dangerous_files)

        self.dangerous_directories = (
            self.__class__.dangerous_directories.copy()
        )
        if additional_dangerous_directories:
            self.dangerous_directories.extend(additional_dangerous_directories)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for bash command execution.

        This method implements Bash-specific permission checks:
        0. Injection risk check (detect dynamic shell structures)
        1. Read-only command check (auto-allow safe commands)
        2. Dangerous command pattern check (safety check, bypass-immune)
        3. Sed constraint check (safety check, bypass-immune)
        4. Dangerous path check for config files (safety check, bypass-immune)
        5. Dangerous removal path check for system dirs (bypass-immune)
        6. ACCEPT_EDITS mode filesystem command check

        Args:
            tool_input (`dict[str, Any]`):
                The tool input containing "command" key
            context (`PermissionContext`):
                The permission context with mode and rules

        Returns:
            `PermissionDecision`:
                ALLOW for safe operations, ASK for dangerous operations,
                PASSTHROUGH to let Engine continue with rule matching
        """

        command = tool_input.get("command", "")
        if not command:
            return PermissionDecision(
                behavior=PermissionBehavior.PASSTHROUGH,
                message="Empty command",
            )

        # 0. Injection check: detect dynamic shell structures that cannot be
        # statically analyzed (command substitution, process substitution,
        # control flow, etc.). Must run before read-only check so that
        # `$(rm -rf /)` inside an otherwise-safe command is caught.
        injection_reason = self._bash_parser.check_injection_risk(command)
        if injection_reason:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: {injection_reason}",
                decision_reason="Safety check: command contains dynamic "
                "expansion that cannot be statically analyzed",
            )

        # 1. Check if command is read-only (auto-allow)
        if self._bash_parser.is_read_only_command(command):
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Permission granted for read-only command",
                decision_reason="Read-only command is allowed",
            )

        # 2. Check for dangerous commands (safety check, bypass-immune)
        dangerous_pattern = self._bash_parser.check_dangerous_command(command)
        if dangerous_pattern:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: Command contains dangerous "
                f"pattern: {dangerous_pattern}",
                decision_reason="Safety check: dangerous command pattern "
                "detected",
            )

        # 3. Check for sed constraints (safety check, bypass-immune)
        sed_error = self._bash_parser.check_sed_constraints(
            command,
            self.dangerous_files,
        )
        if sed_error:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: {sed_error}",
                decision_reason="Safety check: sed in-place modification "
                "of dangerous file",
            )

        # 4. Check for dangerous paths in sensitive config files/dirs
        # (safety check, bypass-immune)
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

        # 5. Check for dangerous removal paths: rm/rmdir targeting system
        # critical directories like /, /usr, /etc, ~ (bypass-immune).
        # Checked separately from step 4 because these paths are not in the
        # dangerous_files/directories lists — they are system-level paths
        # that should never be removed regardless of user configuration.
        removal_path = self._check_dangerous_removal_path(command)
        if removal_path:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Dangerous removal operation detected: "
                f"'{removal_path}'\n\nThis command would remove a critical "
                f"system directory. This requires explicit approval and "
                f"cannot be auto-allowed by permission rules.",
                decision_reason="Safety check: dangerous removal of "
                "critical system path",
            )

        # 6. Check ACCEPT_EDITS mode for filesystem commands
        if context.mode == PermissionMode.ACCEPT_EDITS:
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
                )

        # 6. Passthrough to let Engine continue with rule matching
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message=f"Execute bash command: {command}",
        )

    def match_rule(
        self,
        rule_content: str,
        tool_input: dict[str, Any],
    ) -> bool:
        r"""Match Bash command using regex-based wildcard matching.

        Implements Claude Code's wildcard matching with escape sequences:
        - Supports \* for literal asterisk and \\ for literal backslash
        - Special optimization: "git *" matches both "git" and "git add"
        - Prefix pattern (e.g., "git:*"): matches commands starting with "git "
        - Wildcard pattern: converts to regex with proper escape handling
        - Substring pattern: exact substring matching

        Args:
            rule_content: The command pattern to match
            tool_input: Must contain a "command" key with the command string

        Returns:
            True if pattern matches the command
        """
        import re

        command = tool_input.get("command", "")

        # Check if pattern is a prefix pattern (ends with :*)
        if rule_content.endswith(":*"):
            prefix = rule_content[:-2].strip()
            return command.startswith(prefix + " ") or command == prefix

        # Check if pattern has unescaped wildcards
        def has_wildcards(pattern: str) -> bool:
            """Check if pattern contains unescaped * wildcards."""
            i = 0
            while i < len(pattern):
                if pattern[i] == "\\":
                    i += 2  # Skip escaped character
                elif pattern[i] == "*":
                    return True
                else:
                    i += 1
            return False

        if not has_wildcards(rule_content):
            # No wildcards, but may have escape sequences
            # Convert escape sequences for matching
            pattern = rule_content
            pattern = pattern.replace("\\\\", "\x00BACKSLASH\x00")
            pattern = pattern.replace("\\*", "*")
            pattern = pattern.replace("\x00BACKSLASH\x00", "\\")
            # Use substring matching with converted pattern
            return pattern in command

        # Convert wildcard pattern to regex with escape handling
        # Use placeholders for escaped sequences
        ESCAPED_STAR = "\x00ESCAPED_STAR\x00"
        ESCAPED_BACKSLASH = "\x00ESCAPED_BACKSLASH\x00"

        pattern = rule_content
        # Replace \\ with placeholder
        pattern = pattern.replace("\\\\", ESCAPED_BACKSLASH)
        # Replace \* with placeholder
        pattern = pattern.replace("\\*", ESCAPED_STAR)

        # Manually escape regex special characters (except *)
        # Don't use re.escape() as it escapes spaces too
        special_chars = r".^$+?{}[]|()"
        for char in special_chars:
            pattern = pattern.replace(char, "\\" + char)

        # Convert * to regex .* (match any characters)
        pattern = pattern.replace("*", ".*")

        # Restore escaped sequences
        pattern = pattern.replace(ESCAPED_STAR, r"\*")
        pattern = pattern.replace(ESCAPED_BACKSLASH, r"\\")

        # Special optimization: "git *" should match both "git" and "git add"
        # Pattern: if ends with .*, make it optional
        if pattern.endswith(".*"):
            base_pattern = pattern[:-2]  # Remove .*
            # Try exact match first (handles trailing space)
            base_pattern = base_pattern.rstrip()
            if re.fullmatch(base_pattern, command):
                return True

        # Full regex match
        try:
            return bool(re.fullmatch(pattern, command))
        except re.error:
            # Invalid regex, fall back to substring matching
            return rule_content.replace("*", "") in command

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List["PermissionRule"]:
        """Generate suggested permission rules for Bash commands.

        Generates prefix rules based on command + subcommand (two words).
        For example, "git commit -m 'xxx'" generates "git commit:*".

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing "command" key

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules based on command prefixes
        """
        from .._permission import PermissionRule

        command = tool_input.get("command", "")
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

    def _extract_dangerous_paths_from_bash(
        self,
        command: str,
    ) -> list[str]:
        """Extract dangerous paths from a bash command using tree-sitter.

        Checks for dangerous paths in:
        - File-manipulating commands (rm, mv, cp, chmod, chown, sed, touch)
        - Output redirections (>, >>)

        Args:
            command (`str`):
                The bash command string

        Returns:
            `list[str]`:
                List of dangerous paths found in the command
        """
        dangerous_paths = []

        # Use tree-sitter to extract file paths
        file_paths = self._bash_parser.extract_file_paths(command)

        for _cmd_name, path in file_paths:
            if self._is_dangerous_path(path):
                dangerous_paths.append(path)

        return dangerous_paths

    def _check_dangerous_removal_path(self, command: str) -> str | None:
        """Check if an rm/rmdir command targets a critical system path.

        Detects commands like `rm -rf /`, `rm -rf /usr`, `rmdir ~` that
        would destroy critical system directories. Unlike _is_dangerous_path
        (which checks against a configurable list of sensitive config files),
        this checks against a fixed set of system-level paths that must
        never be removed regardless of user configuration.

        Dangerous paths are:
        - Root directory (/)
        - Home directory (~)
        - Wildcard alone (*) or as dir/* (removes everything)
        - Direct children of root (/usr, /etc, /tmp, /var, etc.)

        Args:
            command (`str`):
                The bash command string

        Returns:
            `str | None`:
                The dangerous path if found, None otherwise
        """
        tokens = command.strip().split()
        if not tokens:
            return None

        # Find rm or rmdir subcommands (handle compound commands)
        try:
            tree = self._bash_parser.parser.parse(bytes(command, "utf8"))
            subcommands = self._bash_parser.split_compound_command(
                tree.root_node,
                command,
            )
        except Exception:
            subcommands = [command]

        # Check each subcommand for rm/rmdir
        for subcmd in subcommands:
            subcmd_tokens = subcmd.strip().split()
            if not subcmd_tokens:
                continue
            base = subcmd_tokens[0]
            if base not in ("rm", "rmdir"):
                continue

            # Collect non-flag arguments as potential paths
            i = 1
            while i < len(subcmd_tokens):
                tok = subcmd_tokens[i]
                # Skip flags
                if tok.startswith("-"):
                    i += 1
                    continue
                path = tok.strip("'\"")
                if self._is_dangerous_removal_path(path):
                    return path
                i += 1

        return None

    def _is_dangerous_removal_path(self, path: str) -> bool:
        """Check if a path is a critical system directory that must not be
        removed.

        Args:
            path (`str`):
                The path to check (may be relative, absolute, or contain ~)

        Returns:
            `bool`:
                True if removing this path would be catastrophic
        """
        import os

        # Bare wildcard
        if path in ("*", "./*", "/"):
            return True
        # Ends with /* — removes everything in a directory
        if path.endswith("/*") or path.endswith("\\*"):
            return True

        # Expand tilde and resolve to absolute path
        expanded = os.path.expanduser(path)
        # Don't resolve symlinks — /tmp is a symlink on macOS but is
        # still a root-child and should be flagged
        abs_path = os.path.normpath(os.path.abspath(expanded))

        # Home directory
        home = os.path.expanduser("~")
        if abs_path == home:
            return True

        # Root itself
        if abs_path == "/":
            return True

        # Direct children of root: /usr, /etc, /tmp, /var, /bin, etc.
        parent = os.path.dirname(abs_path)
        if parent == "/":
            return True

        return False

    async def __call__(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the bash and return the output.

        Args:
            command: The bash command to execute.
            description: Optional description of what the command does.
            timeout: Timeout in milliseconds (default: 120000, max: 600000).

        Yields:
            ToolChunk: The tool execution result with stdout/stderr content.
        """
        import asyncio
        from ...message import TextBlock

        # Clamp timeout to max 600000ms and convert to seconds
        timeout_ms = min(timeout, 600000)
        timeout_sec = timeout_ms / 1000.0

        try:
            # Create subprocess
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion with timeout
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )

            # Decode and normalize line endings
            stdout = stdout_bytes.decode("utf-8", errors="replace").replace(
                "\r\n",
                "\n",
            )
            stderr = stderr_bytes.decode("utf-8", errors="replace").replace(
                "\r\n",
                "\n",
            )

            # Combine output
            output = stdout
            if stderr:
                if output:
                    output += "\n"
                output += stderr

            # Truncate if exceeds 30000 characters
            if len(output) > 30000:
                output = output[:30000] + "\n... (output truncated)"

            # Check exit code
            if process.returncode != 0:
                # Command failed
                result = f"Command failed: {command}\n"
                if stdout:
                    result += f"\nStdout:\n{stdout}"
                if stderr:
                    result += f"\nStderr:\n{stderr}"

                # Truncate error message if needed
                if len(result) > 30000:
                    result = result[:30000] + "\n... (output truncated)"

                yield ToolChunk(
                    content=[TextBlock(text=result)],
                    state="error",
                    is_last=True,
                )
            else:
                # Command succeeded - note: ToolChunk uses "running" state
                # which will be converted to "finished" in ToolResponse
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state="running",
                    is_last=True,
                )

        except asyncio.TimeoutError:
            # Timeout occurred
            error_msg = f"Command timed out after {timeout_ms}ms: {command}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state="error",
                is_last=True,
            )

        except Exception as e:
            # Other errors
            error_msg = f"Command failed: {command}\nError: {str(e)}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state="error",
                is_last=True,
            )
