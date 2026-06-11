# -*- coding: utf-8 -*-
"""Remote built-in tools for DockerWorkspace and E2BWorkspace.

These tools wrap a workspace's ``_exec`` / ``_read`` / ``_write`` primitives
so that agents can interact with a remote sandbox (Docker container or E2B
cloud sandbox) without going through an MCP gateway.
"""
from __future__ import annotations

import asyncio
import fnmatch
import shlex
from typing import Any, AsyncGenerator, List

from ..permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from ..tool import ToolBase
from ..tool._response import ToolChunk
from ..message import TextBlock, ToolResultState
from .._logging import logger

# Re-use descriptions / schemas from the local built-ins so the agent sees
# identical signatures.
from ..tool._builtin._bash import Bash as _LocalBash
from ..tool._builtin._edit import Edit as _LocalEdit
from ..tool._builtin._read import Read as _LocalRead
from ..tool._builtin._write import Write as _LocalWrite
from ..tool._builtin._glob import Glob as _LocalGlob
from ..tool._builtin._grep import Grep as _LocalGrep


# ── small helpers ──────────────────────────────────────────────────


def _normalize_output(result: Any) -> str:
    """Convert an _ExecResult into a text string."""
    stdout = getattr(result, "stdout", b"")
    stderr = getattr(result, "stderr", b"")
    text = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    if getattr(result, "exit_code", 0) != 0:
        out = f"Command failed (exit {result.exit_code}):\n"
        if text:
            out += f"\nStdout:\n{text}"
        if err:
            out += f"\nStderr:\n{err}"
        return out
    if err:
        if text:
            text += "\n"
        text += err
    return text


def _truncate(text: str, limit: int = 30000) -> str:
    if len(text) > limit:
        return text[:limit] + "\n... (output truncated)"
    return text


# ── RemoteBash ─────────────────────────────────────────────────────


class RemoteBash(ToolBase):
    """Bash tool that executes commands inside a remote workspace."""

    name = _LocalBash.name
    description = _LocalBash.description
    input_schema = _LocalBash.input_schema
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="",
        )

    async def __call__(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        timeout_sec = min(timeout, 600000) / 1000.0
        try:
            result = await self._workspace._exec(command, timeout=timeout_sec)
            output = _truncate(_normalize_output(result))
            if getattr(result, "exit_code", 0) != 0:
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state=ToolResultState.ERROR,
                    is_last=True,
                )
            else:
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state=ToolResultState.RUNNING,
                    is_last=True,
                )
        except asyncio.TimeoutError:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Command timed out after {timeout}ms: {command}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        except Exception as e:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Command failed: {command}\nError: {str(e)}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )


# ── RemoteRead ─────────────────────────────────────────────────────


class RemoteRead(ToolBase):
    """Read tool that reads files from a remote workspace."""

    name = _LocalRead.name
    description = _LocalRead.description
    input_schema = _LocalRead.input_schema
    is_read_only = True
    is_concurrency_safe = True
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace
        self._max_line_characters = 2000

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="File reading is read-only.",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []
        parent = __import__("os").path.dirname(file_path)
        pattern = (parent.rstrip("/") + "/**") if parent else "**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        _agent_state: Any = None,
    ) -> ToolChunk:
        try:
            data = await self._workspace._read(file_path)
        except FileNotFoundError:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error: File does not exist: {file_path}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("utf-8", errors="replace")

        lines = text.splitlines(keepends=True)
        start_idx = max(0, offset - 1)
        end_idx = start_idx + limit
        selected = lines[start_idx:end_idx]

        formatted_lines = []
        for i, line in enumerate(selected, start=offset):
            content = line.rstrip("\n\r")
            if len(content) > self._max_line_characters:
                content = content[: self._max_line_characters] + "[truncated]"
            formatted_lines.append(f"{i:6d}\t{content}")

        result = "\n".join(formatted_lines)
        return ToolChunk(
            content=[TextBlock(text=result)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )


# ── RemoteWrite ────────────────────────────────────────────────────


class RemoteWrite(ToolBase):
    """Write tool that writes files into a remote workspace."""

    name = _LocalWrite.name
    description = _LocalWrite.description
    input_schema = _LocalWrite.input_schema
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []
        parent = __import__("os").path.dirname(file_path)
        pattern = (parent.rstrip("/") + "/**") if parent else "**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        content: str,
        _agent_state: Any = None,
    ) -> ToolChunk:
        try:
            # _write on Docker/E2B already does mkdir -p
            await self._workspace._write(file_path, content.encode("utf-8"))
            line_count = len(content.split("\n"))
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"The file {file_path} has been written successfully "
                        f"({line_count} lines).",
                    ),
                ],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error writing file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )


# ── RemoteEdit ─────────────────────────────────────────────────────


class RemoteEdit(ToolBase):
    """Edit tool that performs string replacement in a remote file."""

    name = _LocalEdit.name
    description = _LocalEdit.description
    input_schema = _LocalEdit.input_schema
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []
        parent = __import__("os").path.dirname(file_path)
        pattern = (parent.rstrip("/") + "/**") if parent else "**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        _agent_state: Any = None,
    ) -> ToolChunk:
        if old_string == new_string:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="Error: old_string and new_string are identical. "
                        "No changes to make.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            data = await self._workspace._read(file_path)
        except FileNotFoundError:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error: File not found: {file_path}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        content = data.decode("utf-8", errors="replace")
        occurrences = content.count(old_string)

        if occurrences == 0:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: old_string not found in {file_path}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if occurrences > 1 and not replace_all:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: old_string appears {occurrences} times in "
                        f"{file_path}. Set replace_all=true to replace all "
                        f"occurrences, or make old_string more specific.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if replace_all:
            updated = content.replace(old_string, new_string)
        else:
            updated = content.replace(old_string, new_string, 1)

        try:
            await self._workspace._write(file_path, updated.encode("utf-8"))
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error writing file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        msg = (
            f"all {occurrences} occurrences"
            if replace_all
            else "1 occurrence"
        )
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"Successfully replaced {msg} in {file_path}",
                ),
            ],
            state=ToolResultState.RUNNING,
            is_last=True,
        )


# ── RemoteGlob ─────────────────────────────────────────────────────


class RemoteGlob(ToolBase):
    """Glob tool that finds files matching a pattern inside a remote workspace."""

    name = _LocalGlob.name
    description = _LocalGlob.description
    input_schema = _LocalGlob.input_schema
    is_read_only = True
    is_concurrency_safe = True
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Glob pattern matching is read-only.",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True
        path = tool_input.get("path", "")
        if path and fnmatch.fnmatch(path, rule_content):
            return True
        pattern = tool_input.get("pattern", "")
        if pattern and fnmatch.fnmatch(pattern, rule_content):
            return True
        return False

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        path = tool_input.get("path", "")
        if not path:
            path = getattr(self._workspace, "workdir", "/")
        pattern = path.rstrip("/") + "/**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
    ) -> ToolChunk:
        base = path or getattr(self._workspace, "workdir", "/")
        # Use find for glob-like matching — good enough for common cases.
        # For **/*.py  →  find base -type f -name "*.py"
        # For **        →  find base -type f
        if pattern == "**":
            cmd = f"find {shlex.quote(base)} -type f 2>/dev/null || true"
        elif "**" in pattern:
            # e.g. "**/*.py"  →  find base -type f -name "*.py"
            suffix = pattern.split("**", 1)[1].lstrip("/")
            cmd = (
                f"find {shlex.quote(base)} -type f "
                f"-name {shlex.quote(suffix)} 2>/dev/null || true"
            )
        else:
            cmd = (
                f"find {shlex.quote(base)} -type f "
                f"-name {shlex.quote(pattern)} 2>/dev/null || true"
            )

        try:
            result = await self._workspace._exec(cmd)
            text = _normalize_output(result)
            files = [line for line in text.splitlines() if line.strip()]
            if not files:
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
                content=[TextBlock(text="\n".join(files))],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error during glob search: {str(e)}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )


# ── RemoteGrep ─────────────────────────────────────────────────────


class RemoteGrep(ToolBase):
    """Grep tool that searches file contents inside a remote workspace."""

    name = _LocalGrep.name
    description = _LocalGrep.description
    input_schema = _LocalGrep.input_schema
    is_read_only = True
    is_concurrency_safe = True
    is_external_tool = False
    is_state_injected = False

    def __init__(self, workspace: Any) -> None:
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Grep search is read-only.",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True
        path = tool_input.get("path", "")
        if not path:
            path = getattr(self._workspace, "workdir", "/")
        return fnmatch.fnmatch(path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        path = tool_input.get("path", "")
        if not path:
            path = getattr(self._workspace, "workdir", "/")
        pattern = path.rstrip("/") + "/**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        pattern: str,
        path: str | None = None,
        output_mode: str = "files_with_matches",
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
        search_path = path or getattr(self._workspace, "workdir", "/")

        # Build rg command
        args: list[str] = ["--hidden"]
        for vcs in [".git", ".svn", ".hg", ".bzr", ".jj", ".sl"]:
            args.extend(["--glob", f"!{vcs}"])
        args.extend(["--max-columns", "500"])

        if multiline:
            args.extend(["-U", "--multiline-dotall"])
        if i or case_insensitive:
            args.append("-i")
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        if n and output_mode == "content":
            args.append("-n")
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

        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        if type is not None:
            args.extend(["--type", type])
        if glob is not None:
            for gp in glob.split(","):
                gp = gp.strip()
                if gp:
                    args.extend(["--glob", gp])

        cmd = f"rg {' '.join(shlex.quote(a) for a in args)} {shlex.quote(search_path)} 2>/dev/null || true"

        try:
            result = await self._workspace._exec(cmd)
            text = _normalize_output(result)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if not lines:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"No matches found for pattern: {pattern}",
                        ),
                    ],
                    state=ToolResultState.SUCCESS,
                    is_last=True,
                )

            effective_limit = head_limit if head_limit is not None else 250
            if effective_limit == 0:
                effective_limit = len(lines)
            sliced = lines[offset : offset + effective_limit]
            was_truncated = len(lines) - offset > effective_limit

            suffix = ""
            if was_truncated:
                suffix = (
                    f"\n\n[Showing results with pagination = "
                    f"limit: {effective_limit}"
                )
                if offset:
                    suffix += f", offset: {offset}"
                suffix += "]"

            return ToolChunk(
                content=[TextBlock(text="\n".join(sliced) + suffix)],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error during grep search: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
