# -*- coding: utf-8 -*-
"""The grep tool in agentscope."""
import os
import re
from typing import AsyncGenerator, Any, Literal

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from ...message import TextBlock


TYPE_EXTENSIONS: dict[str, list[str]] = {
    "js": [".js", ".mjs", ".cjs"],
    "ts": [".ts", ".mts", ".cts"],
    "tsx": [".tsx"],
    "jsx": [".jsx"],
    "py": [".py"],
    "rust": [".rs"],
    "go": [".go"],
    "java": [".java"],
    "cpp": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
    "c": [".c", ".h"],
    "css": [".css"],
    "html": [".html", ".htm"],
    "json": [".json"],
    "md": [".md", ".markdown"],
    "yaml": [".yaml", ".yml"],
    "toml": [".toml"],
    "sh": [".sh", ".bash"],
}


class Grep(ToolBase):
    """The grep tool for searching file contents using regular expressions."""

    name: str = "Grep"
    """The tool name presented to the agent."""

    description: str = """A powerful search tool built on ripgrep

Usage:
- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts per file
- Context lines: use context parameter or -A/-B/-C for lines after/before/around matches
- Case-insensitive search: set case_insensitive to true
- Multiline regex: set multiline to true for patterns spanning multiple lines
- Limit results: use head_limit to cap the number of results returned"""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "The directory or file path to search in. "
                "Defaults to current working directory.",
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode: 'content' shows matching lines, "
                "'files_with_matches' shows only file paths, "
                "'count' shows match counts.",
                "default": "files_with_matches",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g., '*.js', "
                "'**/*.tsx').",
            },
            "type": {
                "type": "string",
                "description": "File type to filter by (e.g., 'js', 'py', "
                "'rust'). See TYPE_EXTENSIONS for supported "
                "types.",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Perform case-insensitive search.",
                "default": False,
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines to show around "
                "matches.",
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline regex matching.",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["pattern"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def __init__(self) -> None:
        """Initialize the grep tool."""

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for grep search.

        Grep is a read-only tool. Return PASSTHROUGH to let the engine
        handle EXPLORE mode and rule matching.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Grep search is read-only.",
        )

    def _match_glob(self, glob_pattern: str, filename: str) -> bool:
        """Match a filename against a glob pattern.

        Args:
            glob_pattern: The glob pattern (e.g., '*.js', '**/*.tsx')
            filename: The filename to match

        Returns:
            True if the filename matches the pattern
        """
        # Convert glob pattern to regex
        # ** matches any number of directories (including zero)
        # * matches any characters except /
        # ? matches a single character
        pattern = glob_pattern.replace(".", r"\.")
        pattern = pattern.replace("?", "<!QUESTION!>")
        pattern = pattern.replace("**/", "<!DOUBLESTAR_SLASH!>")
        pattern = pattern.replace("**", "<!DOUBLESTAR!>")
        pattern = pattern.replace("*", "[^/]*")
        # **/ should match zero or more directory levels
        pattern = pattern.replace("<!DOUBLESTAR_SLASH!>", "(?:.*/)?")
        pattern = pattern.replace("<!DOUBLESTAR!>", ".*")
        pattern = pattern.replace("<!QUESTION!>", ".")
        pattern = f"^{pattern}$"

        return bool(re.match(pattern, filename))

    def _collect_files(
        self,
        base_dir: str,
        glob: str | None = None,
        file_type: str | None = None,
    ) -> list[str]:
        """Collect all files under a base directory, optionally filtered
        by glob or type.

        Args:
            base_dir: The base directory to search from
            glob: Optional glob pattern to filter files by name
            file_type: Optional file type key to filter by extension

        Returns:
            A list of matching file paths
        """
        results: list[str] = []
        extensions = TYPE_EXTENSIONS.get(file_type) if file_type else None

        def walk(dir_path: str) -> None:
            try:
                entries = os.listdir(dir_path)
            except (PermissionError, OSError):
                return

            for entry in entries:
                # Skip hidden files, .git, and node_modules
                if entry.startswith(".") or entry == "node_modules":
                    continue

                full_path = os.path.join(dir_path, entry)

                if os.path.isdir(full_path):
                    walk(full_path)
                elif os.path.isfile(full_path):
                    if extensions:
                        ext = os.path.splitext(entry)[1]
                        if ext in extensions:
                            results.append(full_path)
                    elif glob:
                        # Match glob against relative path from base_dir
                        rel_path = os.path.relpath(full_path, base_dir)
                        # Normalize to use forward slashes for consistency
                        rel_path = rel_path.replace(os.sep, "/")
                        if self._match_glob(glob, rel_path):
                            results.append(full_path)
                    else:
                        results.append(full_path)

        # If base_dir is a file, just return it
        if os.path.isfile(base_dir):
            results.append(base_dir)
        else:
            walk(base_dir)

        return results

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
        case_insensitive: bool = False,
        context: int | None = None,
        multiline: bool = False,
        head_limit: int | None = None,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the grep search and return the results.

        Args:
            pattern: The regex pattern to search for
            path: The directory or file path to search in
            output_mode: Output mode ('content', 'files_with_matches', 'count')
            glob: Glob pattern to filter files
            type: File type to filter by
            case_insensitive: Perform case-insensitive search
            context: Number of context lines to show around matches
            multiline: Enable multiline regex matching
            head_limit: Maximum number of results to return
        """
        # Default to current working directory
        search_path = path or os.getcwd()

        # Collect files to search
        files = self._collect_files(search_path, glob, type)

        # Compile regex pattern
        flags = re.IGNORECASE if case_insensitive else 0
        if multiline:
            flags |= re.MULTILINE | re.DOTALL

        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            yield ToolChunk(
                content=[TextBlock(text=f"Invalid regex pattern: {e}")],
                state="error",
                is_last=True,
            )
            return

        results: list[str] = []

        # Search through files
        for file_path in files:
            try:
                with open(
                    file_path,
                    "r",
                    encoding="utf-8",
                    errors="ignore",
                ) as f:
                    content = f.read()

                if output_mode == "files_with_matches":
                    if regex.search(content):
                        results.append(file_path)
                elif output_mode == "count":
                    matches = regex.findall(content)
                    if matches:
                        results.append(f"{file_path}: {len(matches)}")
                elif output_mode == "content":
                    lines = content.split("\n")
                    matched_lines: set[int] = set()

                    # Find all matching lines
                    for i, line in enumerate(lines):
                        if regex.search(line):
                            matched_lines.add(i)

                    # Add context lines
                    if context is not None:
                        expanded_lines: set[int] = set()
                        for line_num in matched_lines:
                            start = max(0, line_num - context)
                            end = min(len(lines) - 1, line_num + context)
                            for j in range(start, end + 1):
                                expanded_lines.add(j)
                        matched_lines = expanded_lines

                    # Format output
                    for line_num in sorted(matched_lines):
                        results.append(
                            f"{file_path}:{line_num + 1}:{lines[line_num]}",
                        )

            except (PermissionError, OSError, UnicodeDecodeError):
                # Skip unreadable files
                continue

        # Generate output
        if not results:
            yield ToolChunk(
                content=[
                    TextBlock(text=f"No matches found for pattern: {pattern}"),
                ],
                state="running",
                is_last=True,
            )
            return

        # Apply head_limit if specified
        output = results[:head_limit] if head_limit is not None else results

        yield ToolChunk(
            content=[TextBlock(text="\n".join(output))],
            state="running",
            is_last=True,
        )
