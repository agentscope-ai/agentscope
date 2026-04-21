# -*- coding: utf-8 -*-
"""The glob tool in agentscope."""
import os
import re
from typing import Any

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from ...message import TextBlock


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

    def __init__(self) -> None:
        """Initialize the glob tool."""

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
        parts = pattern.split("/")
        self.match_parts(parts, 0, base_dir, results)
        return results

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
        base_dir = path if path else os.getcwd()

        if not os.path.exists(base_dir):
            return ToolChunk(
                content=[TextBlock(text=f"Directory not found: {base_dir}")],
                state="error",
                is_last=True,
            )

        matches = self.glob_match(pattern, base_dir)

        # Sort by modification time (newest first)
        try:
            matches.sort(key=lambda p: os.stat(p).st_mtime, reverse=True)
        except (OSError, FileNotFoundError):
            # If we can't stat some files, just keep the unsorted order
            pass

        if len(matches) == 0:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"No files found matching pattern: {pattern}",
                    ),
                ],
                state="running",
                is_last=True,
            )

        return ToolChunk(
            content=[TextBlock(text="\n".join(matches))],
            state="running",
            is_last=True,
        )
