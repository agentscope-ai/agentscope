# -*- coding: utf-8 -*-
"""The glob tool in agentscope."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from typing import TYPE_CHECKING, Any, List

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from .._base import ToolBase
from .._response import ToolChunk

if TYPE_CHECKING:
    from ._sandbox_backend import SandboxBackend


class Glob(ToolBase):
    """The glob tool for fast file pattern matching."""

    name: str = "Glob"
    """The tool name presented to the agent."""

    description: str = """Fast file pattern matching tool that works with
any codebase size.

Supports glob patterns like "**/*.js" or "src/**/*.ts" and returns
matching file paths sorted by modification time (newest first).

Use this tool when you need to find files by pattern across the
codebase."""  # ignore: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match against "
                "(e.g., '**/*.py', 'src/**/*.ts')",
            },
            "path": {
                "type": "string",
                "description": "The base directory to search from "
                "(defaults to current working directory)",
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
        backend: SandboxBackend | None = None,
    ) -> None:
        """Initialize the glob tool.

        Args:
            backend (`SandboxBackend | None`, optional):
                The sandbox backend to use. When ``None``, a
                :class:`LocalBackend` is created and the high-
                performance local ``os.walk`` + ``os.scandir`` path
                is used. With a remote backend, glob patterns are
                evaluated via a Python script executed through
                ``exec_shell``.
        """
        from ._sandbox_backend import LocalBackend

        self._backend = backend if backend is not None else LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for glob pattern matching.

        Glob is a read-only tool. Return PASSTHROUGH to let the engine
        handle EXPLORE mode and rule matching.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Glob pattern matching is read-only.",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the glob pattern or path.

        Matches rule_content as a glob pattern against the "pattern" or "path"
        parameters. This allows rules to match either the search pattern itself
        or the directory being searched. If rule_content is None, matches all
        invocations (tool-name-level rule).

        Args:
            rule_content (`str | None`):
                Glob pattern to match (e.g., "src/**" to match searches in
                src), or None to match all invocations
            tool_input (`dict[str, Any]`):
                The tool input data containing "pattern" and optional "path"

        Returns:
            `bool`:
                True if the rule matches the pattern or path, False otherwise
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

        # Try matching against the search path first
        path = tool_input.get("path", "")
        if path and fnmatch.fnmatch(path, rule_content):
            return True

        # Fall back to matching against the pattern itself
        pattern = tool_input.get("pattern", "")
        if pattern and fnmatch.fnmatch(pattern, rule_content):
            return True

        return False

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the glob search.

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

        # Normalize path and create pattern
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

    def glob_part_to_regex(self, part: str) -> re.Pattern:
        """Convert a glob pattern part to a regex pattern.

        Args:
            part: A single part of a glob pattern (e.g., '*.py', 'test_??.py')

        Returns:
            A compiled regex pattern
        """
        regex_str = ""
        i = 0
        while i < len(part):
            c = part[i]
            if c == "*":
                regex_str += ".*"
            elif c == "?":
                regex_str += "."
            elif c in ".^$+{}[]|()\\":
                regex_str += "\\" + c
            else:
                regex_str += c
            i += 1
        return re.compile(f"^{regex_str}$")

    def collect_all(self, current_dir: str, results: list[str]) -> None:
        """Recursively collect all files in a directory.

        Args:
            current_dir: The directory to collect files from
            results: The list to append matched file paths to
        """
        try:
            for root, _dirs, files in os.walk(current_dir):
                for file in files:
                    results.append(os.path.join(root, file))
        except (PermissionError, OSError):
            # Skip unreadable directories silently
            pass

    def match_parts(
        self,
        parts: list[str],
        part_index: int,
        current_dir: str,
        results: list[str],
    ) -> None:
        """Recursively match path parts against directory entries.

        Args:
            parts: The split glob pattern parts
            part_index: The current index in the parts array
            current_dir: The current directory being traversed
            results: The list to append matched file paths to
        """
        if part_index >= len(parts):
            return

        part = parts[part_index]
        is_last = part_index == len(parts) - 1

        if part == "**":
            if is_last:
                self.collect_all(current_dir, results)
            else:
                # Match in current directory
                self.match_parts(parts, part_index + 1, current_dir, results)
                # Recursively match in subdirectories
                try:
                    with os.scandir(current_dir) as entries:
                        for entry in entries:
                            if entry.is_dir(follow_symlinks=False):
                                self.match_parts(
                                    parts,
                                    part_index,
                                    entry.path,
                                    results,
                                )
                except (PermissionError, OSError):
                    # Skip unreadable directories silently
                    pass
        else:
            regex = self.glob_part_to_regex(part)
            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        if regex.match(entry.name):
                            full_path = entry.path
                            if is_last:
                                if entry.is_file(follow_symlinks=False):
                                    results.append(full_path)
                            elif entry.is_dir(follow_symlinks=False):
                                self.match_parts(
                                    parts,
                                    part_index + 1,
                                    full_path,
                                    results,
                                )
            except (PermissionError, OSError):
                # Skip unreadable directories silently
                pass

    def glob_match(self, pattern: str, base_dir: str) -> list[str]:
        """Match files against a glob pattern starting from the given
        base directory.

        Args:
            pattern: The glob pattern to match against
            base_dir: The base directory to search from

        Returns:
            A list of matched file paths
        """
        results: list[str] = []
        parts = [p for p in re.split(r"[\\/]+", pattern) if p]
        self.match_parts(parts, 0, base_dir, results)
        return results

    async def _remote_glob(
        self,
        pattern: str,
        base_dir: str,
    ) -> list[str]:
        """Evaluate a glob pattern on a remote backend via a Python script.

        Sends a small inline Python script through ``exec_shell`` that
        uses ``pathlib.Path.glob`` and prints the results. This avoids
        complex shell quoting while being portable across backends.
        """
        # Build a short Python script that globs and prints results
        script = (
            "import pathlib, json, os; "
            f"base = pathlib.Path({base_dir!r}); "
            f"matches = [str(p) for p in base.glob({pattern!r}) "
            "if p.is_file()]; "
            "print(json.dumps(matches))"
        )
        result = await self._backend.exec_shell(
            f"python3 -c {shlex.quote(script)}",
            timeout=30.0,
        )
        if not result.ok():
            return []

        import json

        try:
            return json.loads(result.stdout.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            return []

    async def __call__(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
    ) -> ToolChunk:
        """Execute the glob pattern matching and return the results.

        Args:
            pattern: The glob pattern to match against
            path: Optional base directory to search from (defaults to cwd)

        Returns:
            `ToolChunk`:
                The content contains the matched file paths joined by
                newlines, or an error message if the directory is not found or
                no files match the pattern.
        """
        from ._sandbox_backend import LocalBackend

        base_dir = path if path else os.getcwd()

        if isinstance(self._backend, LocalBackend):
            # Local path: use high-performance os.walk + os.scandir
            if not os.path.exists(base_dir):
                return ToolChunk(
                    content=[
                        TextBlock(text=f"Directory not found: {base_dir}"),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )

            matches = self.glob_match(pattern, base_dir)

            # Sort by modification time (newest first)
            try:
                matches.sort(
                    key=lambda p: os.stat(p).st_mtime,
                    reverse=True,
                )
            except (OSError, FileNotFoundError):
                pass
        else:
            # Remote path: execute glob via backend
            if not await self._backend.file_exists(base_dir):
                return ToolChunk(
                    content=[
                        TextBlock(text=f"Directory not found: {base_dir}"),
                    ],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )

            matches = await self._remote_glob(pattern, base_dir)

            # Sort by mtime via backend
            mtimes: list[tuple[str, float]] = []
            for m in matches:
                mt = await self._backend.stat_mtime(m)
                mtimes.append((m, mt if mt is not None else 0.0))
            mtimes.sort(key=lambda t: t[1], reverse=True)
            matches = [t[0] for t in mtimes]

        if len(matches) == 0:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"No files found matching pattern: {pattern}",
                    ),
                ],
                state=ToolResultState.RUNNING,
                is_last=True,
            )

        return ToolChunk(
            content=[TextBlock(text="\n".join(matches))],
            state=ToolResultState.RUNNING,
            is_last=True,
        )
