# -*- coding: utf-8 -*-
"""Backend-aware read cache tests for Read/Write/Edit tools.

Reproduces #2084: when a file lives only inside a workspace sandbox (e.g.
``DockerWorkspace``), the host filesystem cannot stat the path. Before the
fix, ``ToolContext.cache_file``/``get_cache`` used
``aiofiles.os.path.getmtime`` on the host, which raised for sandbox-only
paths and silently skipped
caching, so a successful ``Read`` was never recorded and the following
``Edit``/``Write`` failed with "you must first read the file".
"""

import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, Write
from agentscope.tool._builtin._backend import BackendBase, ExecResult


class _MemoryBackend(BackendBase):
    """A backend whose paths do NOT exist on the host filesystem.

    Mirrors a sandbox backend (DockerWorkspace/E2B): files live only in
    the backend's in-memory store, so ``aiofiles.os.path.getmtime`` on the
    host raises. ``stat_mtime`` is the backend-aware source of truth.
    """

    def __init__(self) -> None:
        """Store file contents keyed by absolute path."""
        self._files: dict[str, bytes] = {}
        self._mtimes: dict[str, float] = {}

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Pretend any command succeeds (e.g. ``mkdir -p`` for parents)."""
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")

    async def read_file(self, path: str) -> bytes:
        """Return the stored content for ``path``."""
        return self._files[path]

    async def write_file(self, path: str, data: bytes) -> None:
        """Store ``data`` under ``path`` and bump its mtime."""
        self._files[path] = data
        self._mtimes[path] = (self._mtimes.get(path, 0.0) or 0.0) + 1.0

    async def stat_mtime(self, path: str) -> float | None:
        """Return the backend's own mtime, or None if unknown."""
        return self._mtimes.get(path)

    async def file_exists(self, path: str) -> bool:
        """Return whether ``path`` is in the in-memory store."""
        return path in self._files

    async def is_dir(self, path: str) -> bool:
        """These tests never read a directory."""
        return False

    def isabs(self, path: str) -> bool:
        """Treat any path starting with ``/`` as absolute."""
        return path.startswith("/")

    def dirname(self, path: str) -> str:
        """Return the parent directory of ``path``."""
        return os.path.dirname(path) or "/"


class BackendAwareCacheTest(IsolatedAsyncioTestCase):
    """Read cache must work for paths that only exist in the backend."""

    async def asyncSetUp(self) -> None:
        """Build tools backed by a sandbox-only memory backend."""
        self.backend = _MemoryBackend()
        self.read_tool = Read(backend=self.backend)
        self.write_tool = Write(backend=self.backend)
        self.edit_tool = Edit(backend=self.backend)
        self.state = AgentState()
        # A path that does NOT exist on the host filesystem, only in the
        # backend (mirrors a DockerWorkspace path like /workspace/test.txt).
        self.sandbox_path = "/workspace/test.txt"
        await self.backend.write_file(
            self.sandbox_path,
            b"alpha\n",
        )

    async def test_host_cannot_stat_sandbox_path(self) -> None:
        """Sanity: the host filesystem knows nothing about this path."""
        self.assertFalse(os.path.exists(self.sandbox_path))

    async def test_read_populates_cache_for_sandbox_path(self) -> None:
        """Read must cache a file the host cannot stat (#2084)."""
        chunk = await self.read_tool(
            file_path=self.sandbox_path,
            _agent_state=self.state,
        )
        self.assertEqual(chunk.state.value, "running")
        self.assertEqual(len(self.state.tool_context.read_file_cache), 1)
        self.assertEqual(
            self.state.tool_context.read_file_cache[0].file_path,
            self.sandbox_path,
        )

    async def test_edit_after_read_succeeds_for_sandbox_path(self) -> None:
        """The Read->Edit loop from #2084 must complete without retry."""
        await self.read_tool(
            file_path=self.sandbox_path,
            _agent_state=self.state,
        )
        chunk = await self.edit_tool(
            file_path=self.sandbox_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )
        self.assertEqual(chunk.state.value, "running")
        self.assertEqual(
            await self.backend.read_file(self.sandbox_path),
            b"beta\n",
        )

    async def test_write_after_read_succeeds_for_sandbox_path(self) -> None:
        """Write to a read sandbox file must not demand a host-side read."""
        await self.read_tool(
            file_path=self.sandbox_path,
            _agent_state=self.state,
        )
        chunk = await self.write_tool(
            file_path=self.sandbox_path,
            content="gamma\n",
            _agent_state=self.state,
        )
        self.assertEqual(chunk.state.value, "running")
        self.assertEqual(
            await self.backend.read_file(self.sandbox_path),
            b"gamma\n",
        )

    async def test_edit_without_read_still_fails_for_sandbox_path(
        self,
    ) -> None:
        """The 'must read first' guard still fires when nothing is cached."""
        chunk = await self.edit_tool(
            file_path=self.sandbox_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )
        self.assertEqual(chunk.state.value, "error")
        self.assertIn("must first read", chunk.content[0].text)
