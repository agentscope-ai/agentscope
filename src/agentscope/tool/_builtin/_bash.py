# -*- coding: utf-8 -*-
"""The bash tool in agentscope."""
import os
from typing import AsyncGenerator, Any, List
import re

from ._bash_parser import BashCommandParser
from .._base import ToolBase, ToolMiddlewareBase
from .._constants import (
    DEFAULT_DANGEROUS_FILES,
    DEFAULT_DANGEROUS_DIRECTORIES,
)
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
)
from ...message import TextBlock, ToolResultState
from .._response import ToolChunk
from ._backend import BackendBase


class Bash(ToolBase):
    """The bash tool."""

    name: str = "Bash"
    """The tool name presented to the agent."""

    description: str = """执行 bash 命令并返回其输出。

工作目录在命令之间保持不变，但 shell 状态不会保留。shell 环境从用户的
配置文件（bash 或 zsh）初始化。

重要提示：除非被明确指示，或你已经确认专用工具无法完成你的任务，否则避免
使用本工具运行 `find`、`grep`、`cat`、`head`、`tail`、`sed`、`awk` 或
`echo` 命令。请改用相应的专用工具，因为这会为用户带来更好的体验：

 - 文件搜索：使用 Glob（而不是 find 或 ls）
 - 内容搜索：使用 Grep（而不是 grep 或 rg）
 - 读取文件：使用 Read（而不是 cat/head/tail）
 - 编辑文件：使用 Edit（而不是 sed/awk）
 - 写入文件：使用 Write（而不是 echo >/cat <<EOF）
 - 通信：直接输出文本（而不是 echo/printf）

虽然 Bash 工具也能完成类似的操作，但最好使用内置工具，因为它们能提供
更好的用户体验，也更容易审查工具调用和授予权限。

# 说明
 - 如果你的命令会创建新目录或新文件，请先使用本工具运行 `ls`，确认父目录
   存在且位置正确。
 - 在命令中，始终用双引号引用包含空格的路径（例如 cd "path with spaces/file.txt"）。
 - 尽量在整个会话中保持当前工作目录不变：使用绝对路径并避免使用 `cd`。
   只有在用户明确要求时才能使用 `cd`。
 - 你可以指定可选的超时时间（毫秒，最大 600000ms / 10 分钟）。默认情况下，
   你的命令会在 120000ms（2 分钟）后超时。
 - 为你的命令编写清晰、简洁的描述。对于简单命令，请保持简短（5-10 个词）。
   对于复杂命令（管道命令、不常见的参数或任何一眼难以理解的内容），请提供
   足够的上下文，让用户能够理解你的命令将要做什么。
 - 当需要发出多条命令时：
  - 如果命令相互独立且可以并行执行，请在一条消息中发起多个 Bash 工具调用。
    例如：如果需要运行 "git status" 和 "git diff"，请在同一条消息中并行发送
    两个 Bash 调用。
  - 如果命令之间存在依赖关系且必须顺序执行，请用单个 Bash 调用，使用 '&&'
    将它们串联起来。
  - 只有当你需要顺序执行命令且不关心前面的命令是否失败时，才使用 ';'。
  - 不要用换行符分隔命令（在带引号的字符串中允许换行）。
 - 对于 git 命令：
  - 优先创建新的提交，而不是修改（amend）已有的提交。
  - 在执行破坏性操作（例如 git reset --hard、git push --force、git checkout --）
    之前，请考虑是否有更安全的替代方案能达到同样的目的。只有在确实是最佳方案时，
    才使用破坏性操作。
  - 除非用户明确要求，否则绝不跳过钩子（--no-verify）或绕过签名（--no-gpg-sign、
    -c commit.gpgsign=false）。如果钩子失败，请调查并修复根本问题。
 - 避免不必要的 `sleep` 命令：
  - 不要在可以立即执行的命令之间 sleep——直接执行即可。
  - 不要用 sleep 循环重试失败的命令——请诊断根本原因或考虑替代方案。
  - 如果必须 sleep，请保持较短的时长（1-5 秒），以免阻塞用户。"""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 bash 命令。",
            },
            "description": {
                "type": "string",
                "description": (
                    "对此命令作用的清晰、简洁的描述。对于简单命令，请保持"
                    "简短（5-10 个词）。对于复杂命令，请提供足够的上下文。"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "可选的超时时间（毫秒）"
                    "（默认值：120000，最大值：600000）"
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
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(  # pylint: disable=dangerous-default-value
        self,
        dangerous_files: list[str] = DEFAULT_DANGEROUS_FILES,
        dangerous_directories: list[str] = DEFAULT_DANGEROUS_DIRECTORIES,
        cwd: str | os.PathLike[str] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the bash tool.

        Args:
            dangerous_files (`list[str]`, optional):
                Sensitive files that require explicit user confirmation,
                even in BYPASS mode. Matched by basename
                (case-insensitive). Defaults to `DEFAULT_DANGEROUS_FILES`.
                Pass a custom list to fully replace the defaults, or `[]`
                to disable the filename check.
            dangerous_directories (`list[str]`, optional):
                Sensitive directories that require explicit user
                confirmation. Matched when any path segment equals an
                entry (case-insensitive). Defaults to
                `DEFAULT_DANGEROUS_DIRECTORIES`. Pass a custom list to
                fully replace the defaults, or `[]` to disable the
                directory check.
            cwd (`str | os.PathLike[str] | None`, optional):
                The working directory used when executing bash commands.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for shell execution. When
                ``None``, a :class:`LocalBackend` is created.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._bash_parser = BashCommandParser()

        self.dangerous_files = list(dangerous_files)
        self.dangerous_directories = list(dangerous_directories)
        self._cwd = os.fspath(cwd) if cwd is not None else None
        self._backend = backend or LocalBackend()

    async def check_read_only(
        self,
        tool_input: dict[str, Any],
    ) -> bool:
        """Decide whether this specific bash invocation is read-only.

        Inspects the command and returns ``True`` for known-safe read-only
        commands (e.g. ``ls``, ``cat``, ``grep``, ``git status``). The
        static :attr:`is_read_only` class attribute is ``False`` because
        Bash can execute arbitrary commands; this method overrides that
        with a per-invocation answer.
        """
        command = tool_input.get("command", "")
        if not command:
            return self.is_read_only
        # A command with dynamic / unanalyzable structure (command
        # substitution, control flow, ...) cannot be *proven* read-only —
        # e.g. ``ls $(rm -rf /)`` looks read-only but embeds a mutation.
        # Report it as non-read-only so the engine's read-only fast path
        # does not short-circuit it; ``check_permissions`` then surfaces the
        # bypass-immune injection safety ASK.
        if self._bash_parser.check_injection_risk(command):
            return False
        return self._bash_parser.is_read_only_command(command)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for bash command execution.

        This method implements Bash-specific permission checks:

        0. Injection risk check (bypass-immune safety ASK if command
           contains dynamic expansion like ``$(...)`` or ``<(...)``)
        1. Read-only command check — auto-ALLOW in **every mode**
           (including DEFAULT) for known-safe read-only commands
           (``ls``, ``pwd``, ``git status``, ``cat``, etc.). This is
           the static counterpart to :meth:`check_read_only`.
        2. Dangerous command pattern check (bypass-immune safety ASK)
        3. Sed in-place constraint check (bypass-immune safety ASK)
        4. Dangerous path check for config files (bypass-immune safety
           ASK)
        5. Dangerous removal path check for system dirs (bypass-immune
           safety ASK)
        6. ACCEPT_EDITS auto-allow for ``mkdir``/``touch``/``rm``/
           ``rmdir``/``mv``/``cp``/``sed`` — only when **every**
           target path resolves inside a working directory
        7. PASSTHROUGH (engine continues with rule matching)

        "Bypass-immune" decisions set
        :attr:`PermissionDecision.bypass_immune` so they cannot be
        silenced by allow rules in DEFAULT mode. In BYPASS mode all
        bypass-immune ASKs are intentionally skipped — see
        :attr:`PermissionMode.BYPASS`.

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
                bypass_immune=True,
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
                bypass_immune=True,
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
                bypass_immune=True,
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
                bypass_immune=True,
            )

        # 5. Check for dangerous removal paths: rm/rmdir targeting system
        # critical directories like /, /usr, /etc, ~ (bypass-immune).
        # Checked separately from step 4 because these paths are not in the
        # dangerous_files/directories lists — they are system-level paths
        # that should never be removed regardless of user configuration.
        removal_path = await self._check_dangerous_removal_path(command)
        if removal_path:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Dangerous removal operation detected: "
                f"'{removal_path}'\n\nThis command would remove a critical "
                f"system directory. This requires explicit approval and "
                f"cannot be auto-allowed by permission rules.",
                decision_reason="Safety check: dangerous removal of "
                "critical system path",
                bypass_immune=True,
            )

        # 6. Auto-allow filesystem commands whose targets all live inside a
        # working directory. Applies to ACCEPT_EDITS (interactive) and
        # DONT_ASK (its unattended counterpart). Mirrors Write/Edit's strict
        # working-directory check — we never auto-allow a bash command that
        # would touch a path outside the configured working set (e.g.
        # ``cp /etc/hosts /tmp/x`` must not pass even though ``cp`` is in
        # the auto-allow list).
        if context.mode in (
            PermissionMode.ACCEPT_EDITS,
            PermissionMode.DONT_ASK,
        ):
            filesystem_commands = {
                "mkdir",
                "touch",
                "rm",
                "rmdir",
                "mv",
                "cp",
                "sed",
            }
            base_command = (
                command.strip().split()[0] if command.strip() else ""
            )

            if base_command in filesystem_commands:
                # Collect every target path: file arguments AND output
                # redirections. ``extract_file_paths`` includes both.
                target_paths = [
                    path
                    for _cmd, path in self._bash_parser.extract_file_paths(
                        command,
                    )
                ]
                # Conservative: only auto-allow when we extracted at least
                # one target AND every target resolves inside a working
                # directory. An empty list means the parser found nothing
                # actionable (or the command has no args) — in that case
                # we fall through to PASSTHROUGH rather than blindly
                # allowing.
                if target_paths and all(
                    self._path_in_allowed_working_path(path, context)
                    for path in target_paths
                ):
                    return PermissionDecision(
                        behavior=PermissionBehavior.ALLOW,
                        message=f"Permission granted for '{base_command}' "
                        f"command (filesystem command, all targets in "
                        f"working directory)",
                        decision_reason=(
                            f"Filesystem command '{base_command}' is "
                            f"auto-allowed because all target paths are "
                            f"within a working directory"
                        ),
                    )

        # 7. Passthrough to let Engine continue with rule matching
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message=f"Execute bash command: {command}",
        )

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        r"""Match Bash command using regex-based wildcard matching.

        Implements wildcard matching with escape sequences:
        - Supports \* for literal asterisk and \\ for literal backslash
        - Special optimization: "git *" matches both "git" and "git add"
        - Prefix pattern (e.g., "git:*"): matches commands starting with "git "
        - Wildcard pattern: converts to regex with proper escape handling
        - Substring pattern: exact substring matching
        - If rule_content is None, matches all invocations
         (tool-name-level rule)

        Args:
            rule_content: The command pattern to match, or None to match all
            tool_input: Must contain a "command" key with the command string

        Returns:
            True if pattern matches the command
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

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

    async def generate_suggestions(
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

    async def _check_dangerous_removal_path(self, command: str) -> str | None:
        """Check if a rm/rmdir command targets a critical system path.

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
                if await self._is_dangerous_removal_path(path):
                    return path
                i += 1

        return None

    async def _is_dangerous_removal_path(self, path: str) -> bool:
        """Check if a path is a critical system directory that must not be
        removed.

        All path resolution is performed via the backend so that the
        check operates on the **backend environment's** ``$HOME`` /
        ``cwd`` / path semantics, not the host process's.

        Args:
            path (`str`):
                The path to check (may be relative, absolute, or contain ~)

        Returns:
            `bool`:
                True if removing this path would be catastrophic
        """

        # Bare wildcard
        if path in ("*", "./*", "/"):
            return True
        # Ends with /* — removes everything in a directory
        if path.endswith("/*") or path.endswith("\\*"):
            return True

        # Expand tilde and resolve to an absolute path inside the
        # backend environment.  Don't resolve symlinks — ``/tmp`` is a
        # symlink on macOS but is still a root-child and should be
        # flagged.
        expanded = await self._backend.expanduser(path)
        backend_cwd = await self._backend.getcwd()
        abs_path = self._backend.abspath(expanded, cwd=backend_cwd)

        # Home directory
        home = await self._backend.expanduser("~")
        if abs_path == home:
            return True

        # Root itself: ``dirname(root) == root`` on both POSIX
        # (``"/"``) and Windows (``"C:\\"``), so this check is
        # path-flavor agnostic.
        parent = self._backend.dirname(abs_path)
        if abs_path == parent:
            return True

        # Direct children of root (e.g. ``/usr``, ``/etc``, ``/tmp``):
        # the *parent* of these is the root, where
        # ``dirname(parent) == parent``.
        if self._backend.dirname(parent) == parent:
            return True

        return False

    async def call(  # type: ignore[override] # pylint: disable=unused-argument
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

        # Clamp timeout to max 600000ms and convert to seconds
        timeout_ms = min(timeout, 600000)
        timeout_sec = timeout_ms / 1000.0

        try:
            # ``command`` is a full shell command line (it may contain
            # pipes, redirects, ``&&``, …), so wrap it in a shell — the
            # backend primitive runs the argv directly without one. Pick
            # the platform's native shell so the Windows experience that
            # ``main`` had (commands interpreted by ``cmd.exe``) is
            # preserved; POSIX hosts use ``/bin/sh``.
            if os.name == "nt":
                shell_command = ["cmd", "/c", command]
            else:
                shell_command = ["/bin/sh", "-c", command]
            result = await self._backend.exec_shell(
                shell_command,
                cwd=self._cwd,
                timeout=timeout_sec,
            )

            # Decode and normalize line endings
            stdout = result.stdout.decode(
                "utf-8",
                errors="replace",
            ).replace("\r\n", "\n")
            stderr = result.stderr.decode(
                "utf-8",
                errors="replace",
            ).replace("\r\n", "\n")

            # Check for timeout (backend returns exit_code=-1,
            # stderr=b"timed out")
            if result.exit_code == -1 and result.stderr == b"timed out":
                error_msg = (
                    f"Command timed out after {timeout_ms}ms: {command}"
                )
                yield ToolChunk(
                    content=[TextBlock(text=error_msg)],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
                return

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
            if not result.ok():
                # Command failed
                error_result = f"Command failed: {command}\n"
                if stdout:
                    error_result += f"\nStdout:\n{stdout}"
                if stderr:
                    error_result += f"\nStderr:\n{stderr}"

                # Truncate error message if needed
                if len(error_result) > 30000:
                    error_result = (
                        error_result[:30000] + "\n... (output truncated)"
                    )

                yield ToolChunk(
                    content=[TextBlock(text=error_result)],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
            else:
                # Command succeeded - note: ToolChunk uses "running" state
                # which will be converted to "finished" in ToolResponse
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state=ToolResultState.RUNNING,
                    is_last=True,
                )

        except Exception as e:
            # Other errors
            error_msg = f"Command failed: {command}\nError: {str(e)}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state=ToolResultState.ERROR,
                is_last=True,
            )
