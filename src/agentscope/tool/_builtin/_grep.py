# -*- coding: utf-8 -*-
"""The grep tool in agentscope."""
import fnmatch
from typing import Any, List, Literal

from .._base import ToolBase, ToolMiddlewareBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState
from ._backend import BackendBase

# Version control system directories to exclude from searches
VCS_DIRECTORIES_TO_EXCLUDE = [
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    ".jj",
    ".sl",
]

# Default cap on grep results when head_limit is unspecified
DEFAULT_HEAD_LIMIT = 250


class RipgrepTimeoutError(Exception):
    """Custom error class for ripgrep timeouts."""

    def __init__(self, message: str, partial_results: list[str]):
        super().__init__(message)
        self.partial_results = partial_results


class Grep(ToolBase):
    """The grep tool for searching file contents using ripgrep."""

    name: str = "Grep"
    """The tool name presented to the agent."""

    description: str = """基于 ripgrep 构建的强大搜索工具

  用法：
- 搜索任务一律使用 Grep。绝不要以 Bash 命令的方式调用 `grep` 或 `rg`。Grep 工具
  已经针对正确的权限和访问进行了优化。
- 支持完整的正则表达式语法（例如 "log.*Error"、"function\\s+\\w+"）
- 使用 glob 参数（例如 "*.js"、"**/*.tsx"）或 type 参数（例如 "js"、"py"、"rust"）
  过滤文件
- 输出模式："content" 显示匹配行，"files_with_matches" 只显示文件路径（默认），
  "count" 显示每个文件的匹配数量
- 上下文行：使用 context 参数或 -A/-B/-C 指定匹配之后/之前/周围的上下文行数
- 不区分大小写的搜索：将 i 设置为 true
- 多行正则：对于跨越多行的模式，将 multiline 设置为 true
- 限制结果数：使用 head_limit 限制返回的结果数量"""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要在文件内容中搜索的正则表达式模式。",
            },
            "path": {
                "type": "string",
                "description": "要搜索的文件或目录。默认"
                "为当前工作目录。",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "输出模式：'content' 显示匹配行 "
                "（支持 -A/-B/-C 上下文、-n 行号、"
                "head_limit），'files_with_matches' 显示文件"
                "路径（支持 head_limit），'count' 显示"
                "匹配数量（支持 head_limit）。"
                "默认为 'files_with_matches'。",
                "default": "files_with_matches",
            },
            "glob": {
                "type": "string",
                "description": "用于过滤文件的 glob 模式（例如 '*.js'、"
                "'*.{ts,tsx}'）。",
            },
            "type": {
                "type": "string",
                "description": "要搜索的文件类型（rg --type）。"
                "常见类型：js、py、rust、go、java 等。",
            },
            "-A": {
                "type": "integer",
                "description": "每个匹配项之后要显示的行数。"
                "要求 output_mode 为 'content'。",
            },
            "-B": {
                "type": "integer",
                "description": "每个匹配项之前要显示的行数。"
                "要求 output_mode 为 'content'。",
            },
            "-C": {
                "type": "integer",
                "description": "context 的别名。",
            },
            "context": {
                "type": "integer",
                "description": "匹配项前后要显示的上下文行数。要求 "
                "output_mode 为 'content'。",
            },
            "n": {
                "type": "boolean",
                "description": "在输出中显示行号。要求 "
                "output_mode 为 'content'。默认为 true。",
                "default": True,
            },
            "i": {
                "type": "boolean",
                "description": "不区分大小写的搜索。",
                "default": False,
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "不区分大小写的搜索（i 的别名）。",
                "default": False,
            },
            "multiline": {
                "type": "boolean",
                "description": "启用多行模式，其中 . 匹配 "
                "换行符，且模式可以跨越多行。"
                "默认值：false。",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "将输出限制为前 N 行/条目。"
                "未指定时默认为 250。"
                "传 0 表示不限制。",
                "minimum": 0,
            },
            "offset": {
                "type": "integer",
                "description": "在应用 head_limit 之前跳过前 "
                "N 行/条目。默认为 0。",
                "default": 0,
                "minimum": 0,
            },
        },
        "required": ["pattern"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the grep tool.

        Args:
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for shell execution. When
                ``None``, a :class:`LocalBackend` is created.
                Ripgrep is always invoked via ``exec_shell`` so that
                the same code path works for local, Docker, and E2B
                backends.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._backend = backend or LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for grep search."""
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Grep search is read-only.",
        )

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the grep search path.

        Matches rule_content as a glob pattern against the "path" parameter.
        If no path is given, falls back to the current working directory.
        If rule_content is None, matches all invocations (tool-name-level
        rule).

        Args:
            rule_content (`str | None`):
                Glob pattern to match against the search path (e.g., "src/**"),
                or None to match all invocations
            tool_input (`dict[str, Any]`):
                The tool input data containing optional "path" key

        Returns:
            `bool`:
                True if the glob pattern matches the search path, False
                otherwise
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

        path = tool_input.get("path", "")
        if not path:
            path = await self._backend.getcwd()
        return fnmatch.fnmatch(path, rule_content)

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the grep search path.

        Suggests a rule based on the search path. If no path is provided,
        suggests a rule for the current directory.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing optional "path" key

        Returns:
            `List[PermissionRule]`:
                A single suggested rule covering the search directory
        """
        backend_cwd = await self._backend.getcwd()
        path = tool_input.get("path") or backend_cwd

        abs_path = self._backend.abspath(path, cwd=backend_cwd)
        # Glob patterns are POSIX-style strings (matched by fnmatch),
        # not real filesystem paths — do NOT use backend.join_path here.
        pattern = abs_path.rstrip("/\\") + "/**"

        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    def _apply_head_limit(
        self,
        items: list[str],
        limit: int | None,
        offset: int = 0,
    ) -> tuple[list[str], int | None]:
        """Apply head_limit and offset to a list of items.

        Returns (sliced_items, applied_limit_if_truncated).
        """
        if limit == 0:
            return items[offset:], None
        effective_limit = limit if limit is not None else DEFAULT_HEAD_LIMIT
        sliced = items[offset : offset + effective_limit]
        was_truncated = len(items) - offset > effective_limit
        return sliced, (effective_limit if was_truncated else None)

    async def _run_ripgrep(
        self,
        args: list[str],
        search_path: str,
        timeout: int = 30,
    ) -> list[str]:
        """Run ripgrep and return output lines.

        Builds an argument vector and dispatches it through
        ``backend.exec_shell`` (which runs the program directly, without
        a shell), so the same code path works for local, Docker, and E2B
        backends and needs no platform-specific argument quoting.
        """
        command = ["rg", *args, search_path]

        result = await self._backend.exec_shell(
            command,
            timeout=float(timeout),
        )

        if result.exit_code == -1 and result.stderr == b"timed out":
            raise RipgrepTimeoutError(
                f"Ripgrep search timed out after {timeout} seconds. "
                "Try searching a more specific path or pattern.",
                [],
            )

        # returncode 0 = matches found, 1 = no matches
        if result.exit_code not in (0, 1):
            error_msg = result.stderr.decode(
                "utf-8",
                errors="ignore",
            ).strip()
            raise RuntimeError(
                f"ripgrep error (code {result.exit_code}): {error_msg}",
            )

        raw = result.stdout.decode("utf-8", errors="ignore")

        lines = [
            line.rstrip("\r") for line in raw.split("\n") if line.rstrip("\r")
        ]
        return lines

    async def call(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
        output_mode: Literal[
            "content",
            "files_with_matches",
            "count",
        ] = "files_with_matches",
        glob: str | None = None,
        type: str | None = None,  # pylint: disable=redefined-builtin
        i: bool = False,
        case_insensitive: bool = False,
        context: int | None = None,
        multiline: bool = False,
        head_limit: int | None = None,
        offset: int = 0,
        n: bool = True,
        **kwargs: Any,
    ) -> ToolChunk:
        """Execute the grep search using ripgrep.

        Args:
            pattern: The regex pattern to search for
            path: The directory or file path to search in
            output_mode: Output mode ('content', 'files_with_matches', 'count')
            glob: Glob pattern to filter files
            type: File type to filter by (rg --type)
            i: Case-insensitive search (rg -i)
            case_insensitive: Alias for i (backward compatibility)
            context: Number of context lines around matches
            multiline: Enable multiline regex matching
            head_limit: Maximum number of results to return
            (default 250, 0=unlimited)
            offset: Skip first N results
            n: Show line numbers (content mode only, default True)
            **kwargs: Additional parameters (-A, -B, -C)
        """
        search_path = path or await self._backend.getcwd()

        if head_limit is not None and head_limit < 0:
            return ToolChunk(
                content=[
                    TextBlock(text="Error: head_limit must be non-negative."),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if offset < 0:
            return ToolChunk(
                content=[
                    TextBlock(text="Error: offset must be non-negative."),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        args: list[str] = ["--hidden"]

        # Exclude VCS directories
        for vcs_dir in VCS_DIRECTORIES_TO_EXCLUDE:
            args.extend(["--glob", f"!{vcs_dir}"])

        # Limit line length to prevent base64/minified content
        args.extend(["--max-columns", "500"])

        # Multiline mode
        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        # Case-insensitive (support both i and case_insensitive
        # for compatibility)
        if i or case_insensitive:
            args.append("-i")

        # Output mode flags
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")

        # Line numbers (content mode only)
        if n and output_mode == "content":
            args.append("-n")

        # Context flags (content mode only)
        if output_mode == "content":
            A = kwargs.get("-A")
            B = kwargs.get("-B")
            C = kwargs.get("-C")

            if context is not None:
                args.extend(["-C", str(context)])
            elif C is not None:
                args.extend(["-C", str(C)])
            else:
                if B is not None:
                    args.extend(["-B", str(B)])
                if A is not None:
                    args.extend(["-A", str(A)])

        # Pattern — use -e if it starts with a dash
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        # File type filter
        if type is not None:
            args.extend(["--type", type])

        # Glob filter
        if glob is not None:
            raw_patterns = glob.split()
            glob_patterns: list[str] = []
            for raw in raw_patterns:
                if "{" in raw and "}" in raw:
                    glob_patterns.append(raw)
                else:
                    glob_patterns.extend(p for p in raw.split(",") if p)
            for gp in glob_patterns:
                args.extend(["--glob", gp])

        try:
            results = await self._run_ripgrep(args, search_path)
        except RipgrepTimeoutError as e:
            return ToolChunk(
                content=[TextBlock(text=str(e))],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        except RuntimeError as e:
            return ToolChunk(
                content=[TextBlock(text=str(e))],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if not results:
            return ToolChunk(
                content=[
                    TextBlock(text=f"No matches found for pattern: {pattern}"),
                ],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )

        limited, applied_limit = self._apply_head_limit(
            results,
            head_limit,
            offset,
        )

        suffix = ""
        if applied_limit is not None:
            suffix = (
                f"\n\n[Showing results with pagination = "
                f"limit: {applied_limit}"
            )
            if offset:
                suffix += f", offset: {offset}"
            suffix += "]"

        return ToolChunk(
            content=[TextBlock(text="\n".join(limited) + suffix)],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
