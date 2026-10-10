# -*- coding: utf-8 -*-
"""Read cache tests for files that only exist inside a workspace backend."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, Write
from agentscope.tool._builtin._backend import BackendBase, ExecResult


class _MemoryBackend(BackendBase):
    """A backend whose paths do NOT exist on the host filesystem.

    Mirrors a sandbox backend such as ``DockerWorkspace``: files live only
    in the backend, so the host cannot stat them.
    """

    def __init__(self) -> None:
        """Initialize the in-memory file store."""
        self._files: dict[str, bytes] = {}
        self._mtimes: dict[str, float] = {}

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Pretend any command succeeds, e.g. ``mkdir -p`` for parents."""
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")

    async def read_file(self, path: str) -> bytes:
        """Return the stored content for ``path``."""
        return self._files[path]

    async def write_file(self, path: str, data: bytes) -> None:
        """Store ``data`` under ``path`` and bump its mtime."""
        self._files[path] = data
        self._mtimes[path] = self._mtimes.get(path, 1000.0) + 0.001

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
        # A path that exists only in the backend, mirroring a
        # DockerWorkspace path like /workspace/test.txt.
        self.path = "/workspace/test.txt"
        await self.backend.write_file(self.path, b"alpha\n")

    async def test_read_caches_backend_only_path(self) -> None:
        """Read must cache a path the host filesystem cannot stat."""
        await self.read_tool(file_path=self.path, _agent_state=self.state)

        self.assertListEqual(
            [
                entry.model_dump()
                for entry in self.state.tool_context.read_file_cache
            ],
            [
                {
                    "lines": ["alpha\n"],
                    "updated_at": 1000.001,
                    "bytes": 6 / 1024,
                    "file_path": "/workspace/test.txt",
                },
            ],
        )

    async def test_edit_after_read(self) -> None:
        """Read then Edit must succeed instead of looping, see #2084."""
        await self.read_tool(file_path=self.path, _agent_state=self.state)
        chunk = await self.edit_tool(
            file_path=self.path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "running")
        self.assertEqual(await self.backend.read_file(self.path), b"beta\n")

    async def test_write_after_read(self) -> None:
        """Read then Write must not demand a host-side read."""
        await self.read_tool(file_path=self.path, _agent_state=self.state)
        chunk = await self.write_tool(
            file_path=self.path,
            content="gamma\n",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "running")
        self.assertEqual(await self.backend.read_file(self.path), b"gamma\n")

    async def test_edit_without_read(self) -> None:
        """The "must read first" guard still fires with nothing cached."""
        chunk = await self.edit_tool(
            file_path=self.path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "error")
        self.assertIn("must first read", chunk.content[0].text)

    async def test_edit_after_backend_side_change(self) -> None:
        """A change made inside the backend must invalidate the cache."""
        await self.read_tool(file_path=self.path, _agent_state=self.state)
        await self.backend.write_file(self.path, b"changed\n")

        chunk = await self.edit_tool(
            file_path=self.path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "error")
        self.assertIn("must first read", chunk.content[0].text)
        self.assertListEqual(self.state.tool_context.read_file_cache, [])
