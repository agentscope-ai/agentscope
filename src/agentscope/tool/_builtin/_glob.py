# -*- coding: utf-8 -*-
"""The glob tool in agentscope."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from typing import TYPE_CHECKING, Any, List

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk

if TYPE_CHECKING:
    from ._backend import BackendBase


def _default_glob_helper_path() -> str:
    """Resolve the on-disk path of the bundled ``_glob_helper.py`` script.

    Used by :class:`Glob` when no explicit ``glob_helper_path`` is
    provided (i.e. the local-workspace case). The path is obtained via
    :mod:`importlib.resources` so it works for both editable and
    installed packages.
    """
    import importlib.resources as _res

    ref = _res.files("agentscope.tool._builtin._scripts").joinpath(
        "_glob_helper.py",
    )
    # as_posix() on a MultiplexedPath / PosixPath gives a str path
    return str(ref)


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
        backend: BackendBase | None = None,
        glob_helper_path: str | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the glob tool.

        Args:
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use. When ``None``, a
                :class:`LocalBackend` is created automatically.
            glob_helper_path (`str | None`, optional):
                Filesystem path (inside the backend's environment) to
                the ``_glob_helper.py`` script. When ``None``, the
                path is resolved from the installed package resources
                (suitable for :class:`LocalBackend`). Remote backends
                (Docker, E2B) should pass the path where the script
                was deployed during workspace initialization.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._backend = backend or LocalBackend()
        # When running against the host, invoke the helper with the
        # current interpreter (``sys.executable``) rather than assuming
        # ``python3`` is on PATH.
        self._is_local = isinstance(self._backend, LocalBackend)
        self._glob_helper_path = (
            glob_helper_path
            if glob_helper_path is not None
            else _default_glob_helper_path()
        )

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

    async def call(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
    ) -> ToolChunk:
        """Execute the glob pattern matching and return the results.

        Invokes the standalone ``_glob_helper.py`` script via
        ``exec_shell``. The script performs high-performance
        ``os.walk`` + ``os.scandir`` matching and returns results
        sorted by modification time (newest first) as JSON.

        This unified path works identically across Local, Docker,
        and E2B backends.

        Args:
            pattern (`str`):
                The glob pattern to match against (e.g. ``**/*.py``).
            path (`str | None`, optional):
                Base directory to search from. Defaults to the current
                working directory when ``None``.

        Returns:
            `ToolChunk`:
                On success, the matched file paths joined by newlines
                (or a "no files found" message). If the base directory
                is missing or the helper fails, an error chunk with
                ``ToolResultState.ERROR``.
        """
        base_dir = path if path else os.getcwd()

        # The base must be an existing directory; a regular file would
        # otherwise be accepted here and fail later with a confusing
        # error from the helper.
        if not await self._backend.is_dir(base_dir):
            return ToolChunk(
                content=[
                    TextBlock(text=f"Directory not found: {base_dir}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Invoke the glob helper script via exec_shell as an argv list
        # (run directly, without a shell, so no platform-specific
        # quoting is needed). Use the current interpreter locally
        # (``python3`` may be absent, e.g. on Windows or venvs exposing
        # only ``python``); remote backends run inside Linux images
        # where ``python3`` is the safe choice.
        python = sys.executable if self._is_local else "python3"
        command = [
            python,
            self._glob_helper_path,
            "--pattern",
            pattern,
            "--base-dir",
            base_dir,
        ]
        result = await self._backend.exec_shell(command, timeout=30.0)

        # A non-zero exit means the helper itself failed (missing
        # interpreter/script, permission error, …) — surface it rather
        # than masking it as an empty match.
        if not result.ok():
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Glob helper failed: {stderr}"
                        if stderr
                        else "Glob helper failed with no error output.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            matches = json.loads(
                result.stdout.decode("utf-8", errors="replace"),
            )
        except (json.JSONDecodeError, ValueError):
            matches = []

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
