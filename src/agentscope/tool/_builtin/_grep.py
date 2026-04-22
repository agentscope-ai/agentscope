# -*- coding: utf-8 -*-
"""The grep tool in agentscope."""
import asyncio
import fnmatch
import os
import shutil
from typing import Any, List, Literal

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import TextBlock


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

    description: str = """A powerful search tool built on ripgrep

  Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts per file
- Context lines: use context parameter or -A/-B/-C for lines after/before/around matches
- Case-insensitive search: set i to true
- Multiline regex: set multiline to true for patterns spanning multiple lines
- Limit results: use head_limit to cap the number of results returned"""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search "
                "for in file contents.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults "
                "to current working directory.",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: 'content' shows matching lines "
                "(supports -A/-B/-C context, -n line numbers, "
                "head_limit), 'files_with_matches' shows file "
                "paths (supports head_limit), 'count' shows "
                "match counts (supports head_limit). "
                "Defaults to 'files_with_matches'.",
                "default": "files_with_matches",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g., '*.js', "
                "'*.{ts,tsx}').",
            },
            "type": {
                "type": "string",
                "description": "File type to search (rg --type). "
                "Common types: js, py, rust, go, java, etc.",
            },
            "-A": {
                "type": "integer",
                "description": "Number of lines to show after each match. "
                "Requires output_mode: 'content'.",
            },
            "-B": {
                "type": "integer",
                "description": "Number of lines to show before each match. "
                "Requires output_mode: 'content'.",
            },
            "-C": {
                "type": "integer",
                "description": "Alias for context.",
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines to show before and "
                "after matches. Requires output_mode: "
                "'content'.",
            },
            "n": {
                "type": "boolean",
                "description": "Show line numbers in output. Requires "
                "output_mode: 'content'. Defaults to true.",
                "default": True,
            },
            "i": {
                "type": "boolean",
                "description": "Case insensitive search.",
                "default": False,
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline mode where . matches "
                "newlines and patterns can span lines. "
                "Default: false.",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "Limit output to first N lines/entries. "
                "Defaults to 250 when unspecified. "
                "Pass 0 for unlimited.",
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N lines/entries before applying "
                "head_limit. Defaults to 0.",
                "default": 0,
            },
        },
        "required": ["pattern"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def __init__(self) -> None:
        """Initialize the grep tool."""
        self._rg_path = shutil.which("rg")

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

    def match_rule(
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
            path = os.getcwd()
        return fnmatch.fnmatch(path, rule_content)

    def generate_suggestions(
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
        path = tool_input.get("path", "")
        if not path:
            path = os.getcwd()

        abs_path = os.path.abspath(path)
        pattern = abs_path.rstrip("/") + "/**"

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
        """Run ripgrep and return output lines."""
        full_args: list = [self._rg_path, *args, search_path]

        proc = await asyncio.create_subprocess_exec(
            *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError as e:
            proc.kill()
            await proc.communicate()
            raise RipgrepTimeoutError(
                f"Ripgrep search timed out after {timeout} seconds. "
                "Try searching a more specific path or pattern.",
                [],
            ) from e

        # returncode 0 = matches found, 1 = no matches (both are success)
        if proc.returncode not in (0, 1):
            error_msg = stderr.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(
                f"ripgrep error (code {proc.returncode}): {error_msg}",
            )

        raw = stdout.decode("utf-8", errors="ignore")
        lines = [
            line.rstrip("\r") for line in raw.split("\n") if line.rstrip("\r")
        ]
        return lines

    async def __call__(  # type: ignore[override]
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
        if self._rg_path is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="ripgrep (rg) not found. Please install it: "
                        "macOS: brew install ripgrep | "
                        "Linux: apt/yum install ripgrep | "
                        "Windows: choco install ripgrep",
                    ),
                ],
                state="error",
                is_last=True,
            )

        search_path = path or os.getcwd()

        args: list[str] = ["--hidden"]

        # Exclude VCS directories
        for vcs_dir in VCS_DIRECTORIES_TO_EXCLUDE:
            args.extend(["--glob", f"!{vcs_dir}"])

        # Limit line length to prevent base64/minified content
        args.extend(["--max-columns", "500"])

        # Multiline mode
        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        # Case insensitive (support both i and case_insensitive
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
                state="error",
                is_last=True,
            )
        except RuntimeError as e:
            return ToolChunk(
                content=[TextBlock(text=str(e))],
                state="error",
                is_last=True,
            )

        if not results:
            return ToolChunk(
                content=[
                    TextBlock(text=f"No matches found for pattern: {pattern}"),
                ],
                state="running",
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
            state="running",
            is_last=True,
        )
