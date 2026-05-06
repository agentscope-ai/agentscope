"""Reference backend: real subprocess + temp directory on the host machine.

Not an isolation boundary suitable for untrusted code — only demonstrates
the API and serves as a test backend.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

from .connection import SandboxConnection, register_connection_class
from .exceptions import UnsupportedOperation
from .types import (
    ExecResult,
    SandboxCreateOptions,
    SerializedSandboxState,
)


class LocalTempSandboxConnection(SandboxConnection):
    """Sandbox backed by a host temp directory + ``asyncio.create_subprocess_shell``."""

    def __init__(self, root: Path, *, instance_id: str) -> None:
        self._root = root.resolve()
        self._instance_id = instance_id
        self._destroyed = False

    @property
    def backend_id(self) -> str:
        return "local_temp"

    @property
    def workspace_root(self) -> Path:
        return self._root

    # ─── factory ──────────────────────────────────────────────

    @classmethod
    async def create(cls, options: SandboxCreateOptions) -> LocalTempSandboxConnection:
        if options.backend != "local_temp":
            raise ValueError(f"expected backend 'local_temp', got {options.backend!r}")
        base = Path(options.extra.get("base_dir", "/tmp"))
        base.mkdir(parents=True, exist_ok=True)
        prefix = options.extra.get("prefix", "ws")
        root = base / f"as_sandbox_{prefix}_{uuid.uuid4().hex[:12]}"
        root.mkdir(parents=True, exist_ok=True)
        conn = cls(root, instance_id=uuid.uuid4().hex)
        for cmd in options.startup_commands:
            await conn.exec(cmd, env=options.env)
        return conn

    @classmethod
    async def resume(cls, state: SerializedSandboxState) -> LocalTempSandboxConnection:
        if state.backend != "local_temp":
            raise ValueError("backend mismatch for resume")
        root_s = state.payload.get("root")
        if not isinstance(root_s, str):
            raise ValueError("invalid resume payload: missing root")
        root = Path(root_s)
        if not root.exists():
            raise UnsupportedOperation(f"workspace root no longer exists: {root}")
        iid = state.payload.get("instance_id")
        if not isinstance(iid, str):
            iid = uuid.uuid4().hex
        return cls(root, instance_id=iid)

    # ─── path resolution ─────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """Resolve a sandbox-relative path, ensuring it doesn't escape the root."""
        rel = Path(path)
        if rel.is_absolute():
            rel = Path(*rel.parts[1:]) if rel.parts[0] == "/" else rel
        dest = (self._root / rel).resolve()
        try:
            dest.relative_to(self._root)
        except ValueError as e:
            raise ValueError(f"path escapes sandbox root: {path!r}") from e
        return dest

    # ─── exec ─────────────────────────────────────────────────

    async def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        workdir = self._root if cwd is None else self._resolve(cwd)
        merged_env = {**dict(os.environ), **(env or {})}
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workdir),
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if timeout is None:
                out_b, err_b = await proc.communicate()
            else:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        code = proc.returncode if proc.returncode is not None else -1
        return ExecResult(exit_code=code, stdout=out_b or b"", stderr=err_b or b"")

    # ─── filesystem ───────────────────────────────────────────

    async def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    async def write(self, path: str, data: bytes) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # ─── lifecycle ────────────────────────────────────────────

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)

    async def close(self) -> None:
        """Soft close: mark as closed but don't delete workspace (for pool reuse)."""
        self._destroyed = True

    async def running(self) -> bool:
        return not self._destroyed and self._root.exists()

    # ─── optional: export_state ───────────────────────────────

    async def export_state(self) -> SerializedSandboxState:
        return SerializedSandboxState(
            backend=self.backend_id,
            payload={"root": str(self._root), "instance_id": self._instance_id},
        )


register_connection_class(LocalTempSandboxConnection)
