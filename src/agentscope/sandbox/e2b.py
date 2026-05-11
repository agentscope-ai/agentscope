# -*- coding: utf-8 -*-
"""E2B backend: runs commands inside an E2B cloud sandbox.

Provides cloud-hosted sandbox isolation via the E2B platform. Requires
an E2B API key and the ``e2b`` package to be installed::

    pip install agentscope[sandbox]

The E2B SDK provides a native async API (``AsyncSandbox``) so no thread
pool is needed — all calls are truly async.
"""

import asyncio
import posixpath
import shlex
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .connection import SandboxConnection, register_sandbox_connection_type
from .exceptions import UnsupportedOperation
from .types import (
    SandboxExecutionResult,
    SandboxInitializationConfig,
    SandboxInternalEndpoint,
    SerializedSandboxState,
)


def _import_e2b() -> tuple[Any, Any]:
    """Lazy-import ``e2b`` SDK; raises ``ImportError`` with guidance."""
    try:
        from e2b import AsyncSandbox as _AsyncSandbox
        from e2b import PtySize as _PtySize
    except ImportError as exc:
        raise ImportError(
            "E2BSandboxConnection requires the `e2b` "
            "package. Install with: pip install agentscope[sandbox]",
        ) from exc
    return _AsyncSandbox, _PtySize


_DEFAULT_TEMPLATE = "base"
_DEFAULT_WORKSPACE = "/home/user"
_DEFAULT_TIMEOUT = 300
_DEFAULT_PTY_ROWS = 24
_DEFAULT_PTY_COLS = 80


class E2BSandboxConnection(SandboxConnection):
    """Sandbox backed by the E2B cloud platform.

    The ``workspace`` is the working directory inside the sandbox where
    ``exec``, ``read``, and ``write`` operate relative to.  It defaults
    to ``/home/user``.

    E2B sandboxes natively support exposed ports (via hostname-based
    routing) and snapshot/restore (via the pause/connect API).
    """

    _supports_exposed_ports = True
    _supports_snapshot = True
    _supports_pty = True

    def __init__(
        self,
        sandbox: Any,
        *,
        instance_id: str,
        workspace: str = _DEFAULT_WORKSPACE,
        api_key: str | None = None,
        domain: str | None = None,
    ) -> None:
        """Wrap an existing E2B ``AsyncSandbox`` handle.

        Args:
            sandbox: ``e2b.AsyncSandbox`` instance (already created/connected).
            instance_id: Unique id for this connection instance.
            workspace: Working directory inside the sandbox; all relative
                ``read``/``write`` paths resolve under this.
            api_key: E2B API key (stored for resume/reconnect).
            domain: E2B domain (stored for resume/reconnect).
        """
        self._sandbox = sandbox
        self._instance_id = instance_id
        self._workspace = workspace
        self._api_key = api_key
        self._domain = domain
        self._destroyed = False
        # pid → AsyncCommandHandle for live PTY sessions.
        self._pty_handles: dict[int, Any] = {}

    @property
    def backend_id(self) -> str:
        return "e2b"

    @property
    def workspace_root(self) -> Path:
        """Logical workspace root inside the sandbox."""
        return Path(self._workspace)

    @property
    def sandbox_id(self) -> str:
        """The E2B-assigned sandbox identifier."""
        return self._sandbox.sandbox_id

    # ─── factory ──────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        options: SandboxInitializationConfig,
    ) -> "E2BSandboxConnection":
        """Provision a new E2B sandbox and return a connected instance."""
        if options.backend_id != "e2b":
            raise ValueError(
                f"expected backend 'e2b', got {options.backend_id!r}",
            )
        AsyncSandbox, _ = _import_e2b()

        template: str = options.extra.get("template", _DEFAULT_TEMPLATE)
        workspace: str = options.extra.get("workspace", _DEFAULT_WORKSPACE)
        timeout: int = options.extra.get("timeout", _DEFAULT_TIMEOUT)
        api_key: str | None = options.extra.get("api_key") or None
        domain: str | None = options.extra.get("domain") or None
        metadata: dict[str, str] = options.extra.get("metadata", {})
        envs: dict[str, str] = options.extra.get("envs", {})

        instance_id = uuid.uuid4().hex

        # Merge top-level env with E2B-specific envs.
        merged_envs = {**envs, **options.env} if options.env else envs or None

        create_kwargs: dict[str, Any] = {
            "template": template,
            "timeout": timeout,
        }
        if api_key:
            create_kwargs["api_key"] = api_key
        if domain:
            create_kwargs["domain"] = domain
        if metadata:
            create_kwargs["metadata"] = metadata
        if merged_envs:
            create_kwargs["envs"] = merged_envs

        sandbox = await AsyncSandbox.create(**create_kwargs)

        # Ensure workspace directory exists.
        await _run_command_with_retry(
            sandbox,
            f"mkdir -p {shlex.quote(workspace)}",
            timeout=30,
        )
        conn = cls(
            sandbox,
            instance_id=instance_id,
            workspace=workspace,
            api_key=api_key,
            domain=domain,
        )

        # Run startup commands.
        for cmd in options.startup_commands:
            await conn.exec(cmd, env=options.env)

        return conn

    @classmethod
    async def resume(
        cls,
        state: SerializedSandboxState,
    ) -> "E2BSandboxConnection":
        """Reconnect to an existing E2B sandbox by its sandbox_id.

        If the sandbox was paused, ``AsyncSandbox.connect`` will
        automatically resume it.
        """
        if state.backend_id != "e2b":
            raise ValueError("backend mismatch for resume")
        AsyncSandbox, _ = _import_e2b()

        e2b_sandbox_id = state.payload.get("sandbox_id")
        if not isinstance(e2b_sandbox_id, str):
            raise ValueError(
                "invalid resume payload: missing sandbox_id",
            )

        instance_id = state.payload.get("instance_id")
        if not isinstance(instance_id, str):
            instance_id = uuid.uuid4().hex

        workspace = state.payload.get("workspace", _DEFAULT_WORKSPACE)
        api_key: str | None = state.payload.get("api_key") or None
        domain: str | None = state.payload.get("domain") or None

        connect_kwargs: dict[str, Any] = {
            "sandbox_id": e2b_sandbox_id,
        }
        if api_key:
            connect_kwargs["api_key"] = api_key
        if domain:
            connect_kwargs["domain"] = domain

        try:
            sandbox = await AsyncSandbox.connect(**connect_kwargs)
        except Exception as e:
            raise UnsupportedOperation(
                f"E2B sandbox {e2b_sandbox_id} could not be reconnected: {e}",
            ) from e

        return cls(
            sandbox,
            instance_id=instance_id,
            workspace=workspace,
            api_key=api_key,
            domain=domain,
        )

    # ─── path resolution ─────────────────────────────────────

    def _resolve(self, path: str) -> str:
        """Resolve a sandbox-relative path inside the container workspace."""
        p = PurePosixPath(path)
        if p.is_absolute():
            resolved = posixpath.normpath(p.as_posix())
        else:
            resolved = posixpath.normpath(
                (PurePosixPath(self._workspace) / p).as_posix(),
            )
        ws = posixpath.normpath(self._workspace)
        if resolved != ws and not resolved.startswith(ws + "/"):
            raise ValueError(f"path escapes sandbox workspace: {path!r}")
        return resolved

    # ─── exec ─────────────────────────────────────────────────

    async def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        """Run a shell command inside the E2B sandbox.

        If E2B reports that the sandbox is still pending, wait and retry.
        """
        workdir = self._workspace if cwd is None else self._resolve(cwd)

        run_kwargs: dict[str, Any] = {
            "cwd": workdir,
        }
        if env:
            run_kwargs["envs"] = env
        if not timeout:
            run_kwargs["timeout"] = timeout

        try:
            result = await _run_command_with_retry(
                self._sandbox,
                command,
                **run_kwargs,
            )
        except Exception as e:
            if hasattr(e, "exit_code"):
                return SandboxExecutionResult(
                    exit_code=e.exit_code,
                    stdout=(getattr(e, "stdout", "") or "").encode("utf-8"),
                    stderr=(getattr(e, "stderr", "") or "").encode("utf-8"),
                )
            raise

        return SandboxExecutionResult(
            exit_code=result.exit_code,
            stdout=(result.stdout or "").encode("utf-8"),
            stderr=(result.stderr or "").encode("utf-8"),
        )

    # ─── filesystem ───────────────────────────────────────────

    async def read(self, path: str) -> bytes:
        """Read a file from the E2B sandbox as bytes."""
        sandbox_path = self._resolve(path)
        try:
            data = await self._sandbox.files.read(
                sandbox_path,
                format="bytes",
            )
        except Exception as e:
            raise FileNotFoundError(
                f"file not found in E2B sandbox: {path}",
            ) from e
        return bytes(data)

    async def write(self, path: str, data: bytes) -> None:
        """Write bytes to a file in the E2B sandbox."""
        sandbox_path = self._resolve(path)
        await self._sandbox.files.write(sandbox_path, data)

    # ─── lifecycle ────────────────────────────────────────────

    async def destroy(self) -> None:
        """Kill the E2B sandbox, releasing all cloud resources."""
        if self._destroyed:
            return
        self._destroyed = True
        self._pty_handles.clear()
        try:
            await self._sandbox.kill()
        except Exception:
            pass

    async def close(self) -> None:
        """Soft close: pause the sandbox without killing it (for pool reuse).

        A paused sandbox can be reconnected via ``resume()``.
        """
        if self._destroyed:
            return
        self._destroyed = True
        try:
            await self._sandbox.pause()
        except Exception:
            # If pause fails (e.g. plan doesn't support it), kill instead.
            try:
                await self._sandbox.kill()
            except Exception:
                pass

    async def is_running(self) -> bool:
        """Best-effort liveness check via ``is_running``."""
        if self._destroyed:
            return False
        try:
            return await self._sandbox.is_running()
        except Exception:
            return False

    # ─── capabilities: exposed ports ──────────────────────────

    async def resolve_exposed_port(self, port: int) -> SandboxInternalEndpoint:
        """Resolve a port to the E2B hostname-based endpoint.

        E2B exposes ports via ``{port}-{sandbox_id}.{domain}`` over HTTPS.
        """
        host = self._sandbox.get_host(port)
        return SandboxInternalEndpoint(
            host=host,
            port=443,
            is_tls_enabled=True,
        )

    # ─── capabilities: snapshot ───────────────────────────────

    async def snapshot_workspace(self) -> bytes:
        """Export the workspace directory as a tar archive."""
        result = await self._sandbox.commands.run(
            f"tar cf /tmp/_ws_snapshot.tar -C {self._workspace} .",
            timeout=120,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"snapshot tar failed: {result.stderr}",
            )
        data = await self._sandbox.files.read(
            "/tmp/_ws_snapshot.tar",
            format="bytes",
        )
        # Clean up temp file.
        await self._sandbox.commands.run(
            "rm -f /tmp/_ws_snapshot.tar",
            timeout=10,
        )
        return bytes(data)

    async def restore_workspace(self, data: bytes) -> None:
        """Restore the workspace directory from a tar archive."""
        await self._sandbox.files.write("/tmp/_ws_restore.tar", data)
        rm_cmd = (
            f"rm -rf {self._workspace}/* {self._workspace}"
            "/.[!.]* 2>/dev/null; true"
        )
        await self._sandbox.commands.run(rm_cmd, timeout=30)
        result = await self._sandbox.commands.run(
            f"tar xf /tmp/_ws_restore.tar -C {self._workspace}",
            timeout=120,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"restore tar failed: {result.stderr}",
            )
        await self._sandbox.commands.run(
            "rm -f /tmp/_ws_restore.tar",
            timeout=10,
        )

    # ─── capabilities: PTY ───────────────────────────────────

    async def pty_start(self, command: str, **kwargs: Any) -> int:
        """Start a PTY session inside the E2B sandbox.

        E2B always spawns ``/bin/bash -i -l``; the ``command`` argument
        is sent as initial input once the shell is ready.

        Accepted ``kwargs``:
            rows (int): Terminal rows (default 24).
            cols (int): Terminal columns (default 80).
            cwd  (str): Working directory.
            env  (dict[str, str]): Extra environment variables.

        Returns:
            The PID of the PTY process (used as ``session_id`` for
            subsequent ``pty_write`` calls).
        """
        _, PtySize = _import_e2b()

        rows = int(kwargs.get("rows", _DEFAULT_PTY_ROWS))
        cols = int(kwargs.get("cols", _DEFAULT_PTY_COLS))
        cwd = kwargs.get("cwd")
        env = kwargs.get("env")

        # E2B async PTY requires a callback; we use a no-op here because
        # output is collected on-demand in pty_write via a fresh callback.
        handle = await self._sandbox.pty.create(
            size=PtySize(rows=rows, cols=cols),
            on_data=lambda _data: None,
            cwd=str(cwd) if cwd is not None else None,
            envs=dict(env) if env is not None else None,
        )
        self._pty_handles[handle.pid] = handle

        # If a command was given, send it as initial input.
        if command:
            await self._sandbox.pty.send_stdin(
                handle.pid,
                (command + "\n").encode("utf-8"),
            )

        return handle.pid

    async def pty_write(self, session_id: int, data: str) -> str:
        """Send input to a PTY session and return buffered output.

        Writes ``data`` to the PTY's stdin, waits briefly for output,
        then returns whatever the PTY produced.

        Args:
            session_id: PID returned by :meth:`pty_start`.
            data: Text to send (will be UTF-8 encoded).

        Returns:
            PTY output collected within a short window (may contain
            ANSI escape sequences).  Returns ``""`` if no output
            arrived.
        """
        if session_id not in self._pty_handles:
            raise ValueError(
                f"PTY session {session_id} not found or already closed",
            )

        # Temporarily reconnect to the PTY stream to capture output.
        buf = bytearray()
        got_data = asyncio.Event()

        def _on_data(chunk: bytes) -> None:
            buf.extend(chunk)
            got_data.set()

        # Temporarily reconnect to the PTY stream to capture output.
        reader = await self._sandbox.pty.connect(
            pid=session_id,
            on_data=_on_data,
        )
        try:
            if data:
                await self._sandbox.pty.send_stdin(
                    session_id,
                    data.encode("utf-8"),
                )

            # Give the PTY a short window to produce output.
            try:
                await asyncio.wait_for(got_data.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        finally:
            await reader.disconnect()

        return buf.decode("utf-8", errors="replace")

    async def pty_resize(self, session_id: int, rows: int, cols: int) -> None:
        """Resize a PTY session.

        Args:
            session_id: PID returned by :meth:`pty_start`.
            rows: New terminal row count.
            cols: New terminal column count.
        """
        _, PtySize = _import_e2b()

        if session_id not in self._pty_handles:
            raise ValueError(
                f"PTY session {session_id} not found or already closed",
            )
        await self._sandbox.pty.resize(
            session_id,
            PtySize(rows=rows, cols=cols),
        )

    async def pty_kill(self, session_id: int) -> bool:
        """Kill a PTY session.

        Args:
            session_id: PID returned by :meth:`pty_start`.

        Returns:
            ``True`` if killed, ``False`` if the session was not found.
        """
        if self._pty_handles.pop(session_id, None) is None:
            return False
        try:
            return await self._sandbox.pty.kill(session_id)
        except Exception:
            return False

    # ─── optional: export_state ───────────────────────────────

    async def export_state(self) -> SerializedSandboxState:
        """Serialize connection state for resume via ``connect()``."""
        return SerializedSandboxState(
            backend_id=self.backend_id,
            payload={
                "sandbox_id": self._sandbox.sandbox_id,
                "instance_id": self._instance_id,
                "workspace": self._workspace,
                "api_key": self._api_key or "",
                "domain": self._domain or "",
            },
        )


register_sandbox_connection_type(E2BSandboxConnection)


async def _run_command_with_retry(
    sandbox: Any,
    command: str,
    *,
    retries: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 5.0,
    **kwargs: Any,
) -> Any:
    """Run a command with retry while E2B sandbox is still pending."""
    delay = initial_delay

    for attempt in range(1, retries + 1):
        try:
            return await sandbox.commands.run(command, **kwargs)
        except Exception as e:
            msg = str(e).lower()

            transient = (
                "still pending" in msg
                or "sandbox is pending" in msg
                or "not ready" in msg
                or "sandbox not ready" in msg
            )

            if not transient or attempt >= retries:
                raise

            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_delay)
