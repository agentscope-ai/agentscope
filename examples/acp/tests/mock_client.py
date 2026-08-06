# -*- coding: utf-8 -*-
"""An in-process mock ACP client for the example tests (DESIGN.md §19).

Implements the ``acp.interfaces.Client`` protocol surface the kernel
uses: it records every ``session/update``, answers
``session/request_permission`` from a script, serves ``fs/*`` against
the real filesystem, and backs ``terminal/*`` with real subprocesses —
so Read/Write/Edit/Grep/Bash execute end-to-end against a temp dir.
"""
# pylint: disable=unused-argument  # the Client protocol fixes signatures
import asyncio
from typing import Any

from acp import RequestError
from acp.schema import (
    AllowedOutcome,
    CreateTerminalResponse,
    DeniedOutcome,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalExitStatus,
    TerminalOutputResponse,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)


class _FakeTerminal:
    """One client-owned terminal backed by a real subprocess."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.output = b""
        self._collector: asyncio.Task | None = None

    async def start(
        self,
        command: str,
        args: list[str],
        cwd: str | None,
    ) -> None:
        """Spawn the subprocess and start collecting output."""
        self.proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _collect() -> None:
            assert self.proc is not None and self.proc.stdout is not None
            self.output = await self.proc.stdout.read()

        self._collector = asyncio.create_task(_collect())

    async def wait(self) -> int | None:
        """Wait for exit and the output collector."""
        assert self.proc is not None
        code = await self.proc.wait()
        if self._collector is not None:
            await self._collector
        return code

    def kill(self) -> None:
        """Kill the subprocess if still running."""
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()


class FakeClient:
    """Scripted, filesystem-backed mock of the ACP client side."""

    def __init__(
        self,
        permission_answers: list[str] | None = None,
    ) -> None:
        # Recorded traffic, for assertions.
        self.updates: list[tuple[str, Any]] = []
        self.permission_requests: list[ToolCallUpdate] = []
        # Each entry answers one request_permission call: an option id
        # ("allow_once", ...) or "cancelled" for a DeniedOutcome.
        self.permission_answers = list(permission_answers or [])
        # Set when a permission request arrives and, if present, awaited
        # before answering — lets tests park a turn at the gate.
        self.permission_gate: asyncio.Event | None = None
        self.terminals: dict[str, _FakeTerminal] = {}
        self._terminal_seq = 0
        self.terminal_started = asyncio.Event()

    # ── session/update ────────────────────────────────────────────

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Record one session/update notification."""
        self.updates.append((session_id, update))

    def updates_of(self, kind: str) -> list[Any]:
        """All recorded updates of one sessionUpdate discriminator."""
        return [u for _, u in self.updates if u.session_update == kind]

    # ── permission ────────────────────────────────────────────────

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        """Answer a permission request from the script."""
        self.permission_requests.append(tool_call)
        if self.permission_gate is not None:
            await self.permission_gate.wait()
        answer = (
            self.permission_answers.pop(0)
            if self.permission_answers
            else "allow_once"
        )
        if answer == "cancelled":
            outcome: Any = DeniedOutcome(outcome="cancelled")
        else:
            outcome = AllowedOutcome(outcome="selected", option_id=answer)
        return RequestPermissionResponse(outcome=outcome)

    # ── fs/* ──────────────────────────────────────────────────────

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        """Serve fs/read_text_file from the real filesystem."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ReadTextFileResponse(content=f.read())
        except FileNotFoundError as e:
            raise RequestError.resource_not_found(path) from e

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse:
        """Serve fs/write_text_file onto the real filesystem."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return WriteTextFileResponse()

    # ── terminal/* ────────────────────────────────────────────────

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        """Spawn a real subprocess as the client-owned terminal."""
        self._terminal_seq += 1
        terminal_id = f"term-{self._terminal_seq}"
        terminal = _FakeTerminal()
        await terminal.start(command, list(args or []), cwd)
        self.terminals[terminal_id] = terminal
        self.terminal_started.set()
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        """Wait for the terminal subprocess to exit."""
        code = await self.terminals[terminal_id].wait()
        return WaitForTerminalExitResponse(exit_code=code)

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        """Return the merged output collected so far."""
        terminal = self.terminals[terminal_id]
        exit_status = None
        if terminal.proc is not None and terminal.proc.returncode is not None:
            exit_status = TerminalExitStatus(
                exit_code=terminal.proc.returncode,
            )
        return TerminalOutputResponse(
            output=terminal.output.decode("utf-8", errors="replace"),
            truncated=False,
            exit_status=exit_status,
        )

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse:
        """Kill the terminal subprocess."""
        self.terminals[terminal_id].kill()
        return KillTerminalResponse()

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse:
        """Release (and kill, if needed) the terminal."""
        terminal = self.terminals.get(terminal_id)
        if terminal is not None:
            terminal.kill()
        return ReleaseTerminalResponse()
