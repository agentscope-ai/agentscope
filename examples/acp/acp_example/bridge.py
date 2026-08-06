# -*- coding: utf-8 -*-
"""fs / terminal / permission bridge (DESIGN.md §12, §13, §14).

Three pieces live here:

- :class:`OpRegistry` + :class:`OpBindingMiddleware` — bind every
  backend-level ``fs/*`` / ``terminal/*`` client call to the AgentScope
  ``tool_call_id`` of the enclosing tool call (receipt invariant c).
  The middleware runs *inside* the tool's own task context, so the
  binding survives AgentScope's concurrent tool batches (where a
  ContextVar set from the event-consumer side would not propagate).
- :class:`ClientBackend` — the single ``BackendBase`` implementation
  for shell delegation: ``read_file``/``write_file`` route to the ACP
  client's ``fs/read_text_file``/``fs/write_text_file``, ``exec_shell``
  routes to the client's ``terminal/*`` create→wait→output→release
  cycle.
- :func:`request_permission_for` — converts a
  ``RequireUserConfirmEvent`` into one ``session/request_permission``
  round-trip per gated tool call and maps the outcome back to
  ``ConfirmResult`` objects (receipt invariant d).
"""
import asyncio
import json
import os
import posixpath
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, TYPE_CHECKING

from acp import RequestError
from acp.schema import AllowedOutcome, PermissionOption, ToolCallUpdate

from agentscope.event import ConfirmResult, RequireUserConfirmEvent
from agentscope.message import ToolCallBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionRule,
)
from agentscope.tool import BackendBase, ExecResult, ToolMiddlewareBase

if TYPE_CHECKING:
    from acp.interfaces import Client

    from agentscope.tool import ToolBase, ToolChunk

    from .session import Session

# The tool_call_id of the tool call currently executing in this task
# context, set by OpBindingMiddleware. Read by ClientBackend to stamp
# every outbound fs/terminal client call with its operation id.
_CURRENT_TOOL_CALL_ID: ContextVar[str | None] = ContextVar(
    "acp_example_current_tool_call_id",
    default=None,
)

# Default byte cap for terminal output requested from the client. Keeps
# a runaway command from flooding the JSON-RPC channel; AgentScope's own
# ContextConfig.tool_result_limit caps what re-enters model context.
_TERMINAL_OUTPUT_BYTE_LIMIT = 1024 * 1024


@dataclass
class OpRecord:
    """One receipt-ready record of a client-delegated operation."""

    kind: str
    """``"fs/read"``, ``"fs/write"`` or ``"terminal"``."""

    detail: str
    """The path (fs ops) or the command line (terminal ops)."""

    tool_call_id: str | None
    """The AgentScope tool call this operation belongs to, or ``None``
    for a call made outside any tool."""

    terminal_id: str | None = None
    """The client-side terminal id, for terminal ops."""

    exit_code: int | None = None
    """The exit code, for terminal ops."""


class OpRegistry:
    """Per-session registry binding client calls to tool-call ids.

    ``pending`` holds tool calls whose arguments are complete but whose
    execution has not been claimed by the middleware yet; ``records``
    accumulates the receipt-ready operation log (invariant c).
    """

    def __init__(self) -> None:
        self.pending: dict[str, tuple[str, str]] = {}
        """tool_call_id -> (tool_name, raw JSON input)."""
        self.records: list[OpRecord] = []

    def register_pending(
        self,
        tool_call_id: str,
        tool_name: str,
        raw_input: str,
    ) -> None:
        """Register a fully-streamed tool call awaiting execution."""
        self.pending[tool_call_id] = (tool_name, raw_input)

    def discard_pending(self, tool_call_id: str) -> None:
        """Drop a pending entry (tool finished, was denied, …)."""
        self.pending.pop(tool_call_id, None)

    def claim(self, tool_name: str, input_kwargs: dict) -> str | None:
        """Match a starting tool invocation back to its tool_call_id.

        Called by :class:`OpBindingMiddleware` from inside the tool's
        task. Matching is by tool name first; when several calls to the
        same tool are pending in one batch, the parsed inputs
        disambiguate. The claimed entry is removed so a twin invocation
        cannot bind to the same id twice.
        """
        candidates = [
            (tc_id, raw)
            for tc_id, (name, raw) in self.pending.items()
            if name == tool_name
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            # Disambiguate by comparing the tool's actual kwargs against
            # each pending call's parsed JSON input. Internal kwargs
            # injected by the framework (e.g. _agent_state) are ignored.
            visible = {
                k: v for k, v in input_kwargs.items() if not k.startswith("_")
            }
            for tc_id, raw in candidates:
                try:
                    if json.loads(raw) == visible:
                        candidates = [(tc_id, raw)]
                        break
                except (TypeError, ValueError):
                    continue
        tc_id = candidates[0][0]
        del self.pending[tc_id]
        return tc_id

    def record(self, op: OpRecord) -> None:
        """Append one operation record."""
        self.records.append(op)


class OpBindingMiddleware(ToolMiddlewareBase):
    """Stamps the enclosing tool_call_id into the task context.

    Runs inside the tool's own asyncio task (AgentScope executes
    concurrency-safe tools in parallel worker tasks), so the ContextVar
    value is visible to every backend call the tool makes — including
    the ``file_exists``/``is_dir``/``mkdir -p`` helpers that go through
    ``exec_shell``.
    """

    def __init__(self, ops: OpRegistry) -> None:
        self._ops = ops

    async def on_tool_call(
        self,
        tool: "ToolBase",
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator["ToolChunk", None]],
    ) -> AsyncGenerator["ToolChunk", None]:
        """Bind this invocation to its tool_call_id, then run the tool."""
        tc_id = self._ops.claim(tool.name, input_kwargs)
        token = _CURRENT_TOOL_CALL_ID.set(tc_id)
        try:
            async for chunk in next_handler(**input_kwargs):
                yield chunk
        finally:
            _CURRENT_TOOL_CALL_ID.reset(token)


class ClientBackend(BackendBase):
    """The one shell-delegation backend (DESIGN.md §13).

    Implements all three ``BackendBase`` primitives against the ACP
    client:

    - ``read_file``  → ``fs/read_text_file`` (sees unsaved buffers),
    - ``write_file`` → ``fs/write_text_file``,
    - ``exec_shell`` → ``terminal/create`` → ``wait_for_exit`` →
      ``output`` → ``release``.

    The builtin file tools call ``exec_shell`` for existence/dir/mkdir
    checks, ripgrep and the Glob helper, so shell delegation requires
    the client ``terminal`` capability in addition to ``fs.*``.

    ACP mandates absolute paths: every path is resolved against the
    session ``cwd`` before leaving the process.
    """

    def __init__(
        self,
        conn: "Client",
        session_id: str,
        cwd: str,
        ops: OpRegistry,
    ) -> None:
        self._conn = conn
        self._session_id = session_id
        self._cwd = cwd
        self._ops = ops

    # ── path helpers ──────────────────────────────────────────────

    def _abs(self, path: str) -> str:
        """Resolve ``path`` against the session cwd, POSIX-normalized."""
        if not posixpath.isabs(path) and not os.path.isabs(path):
            path = posixpath.join(self._cwd, path)
        return posixpath.normpath(path)

    async def getcwd(self) -> str:
        """The session working directory, with no client round-trip."""
        return self._cwd

    # ── the three primitives ──────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """Read a text file through the client (unsaved buffers incl.)."""
        abs_path = self._abs(path)
        resp = await self._conn.read_text_file(
            session_id=self._session_id,
            path=abs_path,
        )
        self._ops.record(
            OpRecord(
                kind="fs/read",
                detail=abs_path,
                tool_call_id=_CURRENT_TOOL_CALL_ID.get(),
            ),
        )
        return resp.content.encode("utf-8")

    async def write_file(self, path: str, data: bytes) -> None:
        """Write a text file through the client."""
        abs_path = self._abs(path)
        await self._conn.write_text_file(
            session_id=self._session_id,
            path=abs_path,
            content=data.decode("utf-8"),
        )
        self._ops.record(
            OpRecord(
                kind="fs/write",
                detail=abs_path,
                tool_call_id=_CURRENT_TOOL_CALL_ID.get(),
            ),
        )

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a command in a client-owned terminal.

        The ACP terminal merges stdout and stderr into one stream; the
        merged output is returned as ``stdout``. A timeout kills the
        terminal and reports exit code ``-1`` (the ``ExecResult``
        convention for internal failures).
        """
        run_cwd = self._abs(cwd) if cwd else self._cwd
        create = await self._conn.create_terminal(
            session_id=self._session_id,
            command=command[0],
            args=list(command[1:]),
            cwd=run_cwd,
            output_byte_limit=_TERMINAL_OUTPUT_BYTE_LIMIT,
        )
        terminal_id = create.terminal_id
        record = OpRecord(
            kind="terminal",
            detail=" ".join(command),
            tool_call_id=_CURRENT_TOOL_CALL_ID.get(),
            terminal_id=terminal_id,
        )
        try:
            timed_out = False
            exit_code: int | None = None
            try:
                exit_resp = await asyncio.wait_for(
                    self._conn.wait_for_terminal_exit(
                        session_id=self._session_id,
                        terminal_id=terminal_id,
                    ),
                    timeout=timeout,
                )
                exit_code = exit_resp.exit_code
            except asyncio.TimeoutError:
                timed_out = True
                await self._conn.kill_terminal(
                    session_id=self._session_id,
                    terminal_id=terminal_id,
                )
            out = await self._conn.terminal_output(
                session_id=self._session_id,
                terminal_id=terminal_id,
            )
            if exit_code is None and out.exit_status is not None:
                exit_code = out.exit_status.exit_code
            if timed_out or exit_code is None:
                # Timeout, or terminated by a signal: ExecResult uses -1
                # for internal failures.
                exit_code = -1
            record.exit_code = exit_code
            return ExecResult(
                exit_code=exit_code,
                stdout=out.output.encode("utf-8"),
                stderr=b"",
            )
        finally:
            self._ops.record(record)
            try:
                await self._conn.release_terminal(
                    session_id=self._session_id,
                    terminal_id=terminal_id,
                )
            except (RequestError, ConnectionError):
                # Releasing a terminal the client already dropped (e.g.
                # after a cancelled turn) is not an error worth surfacing.
                print(
                    f"acp_example: release_terminal({terminal_id}) failed",
                    file=sys.stderr,
                )


# ── permission bridge (DESIGN.md §12) ─────────────────────────────────

_PERMISSION_OPTIONS = [
    PermissionOption(option_id="allow_once", name="Allow", kind="allow_once"),
    PermissionOption(
        option_id="allow_always",
        name="Always allow",
        kind="allow_always",
    ),
    PermissionOption(
        option_id="reject_once", name="Reject", kind="reject_once"
    ),
    PermissionOption(
        option_id="reject_always",
        name="Always reject",
        kind="reject_always",
    ),
]


def _parse_raw_input(raw: str) -> Any:
    """Best-effort JSON parse of a streamed tool input for display."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _rules_for(
    tool_call: ToolCallBlock,
    behavior: PermissionBehavior,
) -> list[PermissionRule]:
    """The rules an ``*_always`` decision installs in the kernel.

    For ALLOW, core's own ``suggested_rules`` (attached to the ASKING
    tool call) are preferred — they carry the tool-specific
    ``rule_content`` (a path glob, a command prefix, …). Bypass-immune
    safety ASKs are never silenced by these rules (see #2117), so a
    client may still be prompted again for dangerous operations.
    """
    suggested = getattr(tool_call, "suggested_rules", None) or []
    if behavior == PermissionBehavior.ALLOW and suggested:
        return list(suggested)
    return [
        PermissionRule(
            tool_name=tool_call.name,
            behavior=behavior,
            source="acp-client",
        ),
    ]


async def request_permission_for(
    conn: "Client",
    sess: "Session",
    event: RequireUserConfirmEvent,
) -> list[ConfirmResult] | None:
    """Resolve one ``RequireUserConfirmEvent`` at the ACP client.

    Sends one ``session/request_permission`` per gated tool call, bound
    to the exact ``toolCallId`` (invariant d), and maps each outcome to
    a ``ConfirmResult``. Returns ``None`` when the client reports the
    prompt was cancelled (``{"outcome": "cancelled"}``) — the caller
    then aborts the turn.
    """
    results: list[ConfirmResult] = []
    for tool_call in event.tool_calls:
        resp = await conn.request_permission(
            session_id=sess.id,
            tool_call=ToolCallUpdate(
                tool_call_id=tool_call.id,
                title=tool_call.name,
                raw_input=_parse_raw_input(tool_call.input),
            ),
            options=_PERMISSION_OPTIONS,
        )
        outcome = resp.outcome
        if not isinstance(outcome, AllowedOutcome):
            # DeniedOutcome — the client cancelled the prompt (turn
            # aborted, client shutdown, …). Per spec the whole turn
            # resolves to stopReason "cancelled".
            return None
        confirmed = outcome.option_id in ("allow_once", "allow_always")
        rules: list[PermissionRule] | None = None
        if outcome.option_id == "allow_always":
            rules = _rules_for(tool_call, PermissionBehavior.ALLOW)
        elif outcome.option_id == "reject_always":
            rules = _rules_for(tool_call, PermissionBehavior.DENY)
        results.append(
            ConfirmResult(
                confirmed=confirmed,
                tool_call=ToolCallBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    input=tool_call.input,
                ),
                rules=rules,
            ),
        )
        if not confirmed:
            # A denied call never executes: nothing will claim its
            # pending entry, so drop it here.
            sess.ops.discard_pending(tool_call.id)
    return results
