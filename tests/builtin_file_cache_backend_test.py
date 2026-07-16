# -*- coding: utf-8 -*-
"""Backend-aware cache tests for the builtin file tools."""

import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, Write
from agentscope.tool._builtin._backend import BackendBase, ExecResult


class _RemoteMemoryBackend(BackendBase):
    """A backend whose files are not visible to the host filesystem."""

    def __init__(self) -> None:
        """Initialize the in-memory files and modification times."""
        self.files: dict[str, bytes] = {}
        self.mtimes: dict[str, float] = {}
        self.stat_available = True

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Return success for directory creation used by ``Write``."""
        return ExecResult(exit_code=0, stdout=b"", stderr=b"")

    async def read_file(self, path: str) -> bytes:
        """Read a file from the backend-only store."""
        return self.files[path]

    async def write_file(self, path: str, data: bytes) -> None:
        """Write a file and advance its backend modification time."""
        self.files[path] = data
        self.mtimes[path] = self.mtimes.get(path, 0.0) + 1.0

    async def file_exists(self, path: str) -> bool:
        """Check whether a backend-only file exists."""
        return path in self.files

    async def is_dir(self, path: str) -> bool:
        """The test backend stores files only."""
        return False

    async def stat_mtime(self, path: str) -> float | None:
        """Return the backend mtime when stat is available."""
        if not self.stat_available:
            return None
        return self.mtimes.get(path)


class BackendAwareFileCacheTest(IsolatedAsyncioTestCase):
    """Exercise cache behavior for files that only a backend can access."""

    async def asyncSetUp(self) -> None:
        """Create stateful tools backed by an isolated remote-style store."""
        self.backend = _RemoteMemoryBackend()
        self.read_tool = Read(backend=self.backend)
        self.edit_tool = Edit(backend=self.backend)
        self.write_tool = Write(backend=self.backend)
        self.state = AgentState()
        self.file_path = "/workspace/test.txt"
        await self.backend.write_file(self.file_path, b"alpha\n")
        self.assertFalse(os.path.exists(self.file_path))

    async def _read_file(self) -> None:
        """Read the backend-only file and assert the tool call succeeds."""
        chunk = await self.read_tool(
            file_path=self.file_path,
            _agent_state=self.state,
        )
        self.assertEqual(chunk.state.value, "running")

    async def test_read_caches_backend_only_file(self) -> None:
        """Read should cache a path that the host cannot stat."""
        await self._read_file()

        self.assertEqual(len(self.state.tool_context.read_file_cache), 1)
        self.assertEqual(
            self.state.tool_context.read_file_cache[0].file_path,
            self.file_path,
        )

    async def test_edit_after_read(self) -> None:
        """Edit should accept a backend-only file after Read."""
        await self._read_file()

        chunk = await self.edit_tool(
            file_path=self.file_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "running")
        self.assertEqual(
            await self.backend.read_file(self.file_path),
            b"beta\n",
        )

    async def test_edit_without_read_is_rejected(self) -> None:
        """A backend-only file should still require a preceding Read."""
        chunk = await self.edit_tool(
            file_path=self.file_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "error")
        self.assertIn("must first read", chunk.content[0].text)

    async def test_write_after_read(self) -> None:
        """Write should accept a backend-only file after Read."""
        await self._read_file()

        chunk = await self.write_tool(
            file_path=self.file_path,
            content="beta\n",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "running")
        self.assertEqual(
            await self.backend.read_file(self.file_path),
            b"beta\n",
        )

    async def test_write_without_read_is_rejected(self) -> None:
        """A backend-only existing file should require a preceding Read."""
        chunk = await self.write_tool(
            file_path=self.file_path,
            content="beta\n",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "error")
        self.assertIn("has not been read yet", chunk.content[0].text)

    async def test_backend_change_invalidates_cache(self) -> None:
        """An mtime change in the backend should invalidate cached content."""
        await self._read_file()
        await self.backend.write_file(self.file_path, b"changed externally\n")

        chunk = await self.edit_tool(
            file_path=self.file_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "error")
        self.assertIn("must first read", chunk.content[0].text)
        self.assertEqual(self.state.tool_context.read_file_cache, [])

    async def test_unavailable_backend_mtime_invalidates_cache(self) -> None:
        """An unverifiable cache entry should not authorize an edit."""
        await self._read_file()
        self.backend.stat_available = False

        chunk = await self.edit_tool(
            file_path=self.file_path,
            old_string="alpha",
            new_string="beta",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state.value, "error")
        self.assertIn("must first read", chunk.content[0].text)
        self.assertEqual(self.state.tool_context.read_file_cache, [])

    async def test_agent_state_remains_serializable(self) -> None:
        """Backend-aware caching must not store the backend in agent state."""
        await self._read_file()

        serialized = self.state.model_dump_json()
        restored = AgentState.model_validate_json(serialized)

        self.assertEqual(
            restored.tool_context.read_file_cache[0].file_path,
            self.file_path,
        )
