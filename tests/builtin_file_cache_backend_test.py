# -*- coding: utf-8 -*-
"""Read cache tests for workspace backend files."""
import os
from pathlib import Path
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, ToolChunk, Write
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


class UnknownBackendMtimeTest(IsolatedAsyncioTestCase):
    """A host file and a backend file may have the same absolute path."""

    # pylint: disable=protected-access

    def setUp(self) -> None:
        directory = self.enterContext(
            # pylint: disable-next=consider-using-with
            tempfile.TemporaryDirectory(),
        )
        self.path = str(Path(directory) / "config.txt")
        Path(self.path).write_bytes(b"unrelated host contents\n")
        self.host_mtime = os.stat(self.path).st_mtime
        self.backend = _MemoryBackend()
        # The shared memory fixture normally accepts POSIX sandbox paths.
        self.enterContext(
            patch.object(self.backend, "isabs", side_effect=os.path.isabs),
        )
        self.state = AgentState()
        self.read = Read(backend=self.backend)
        self.edit = Edit(backend=self.backend)
        self.write = Write(backend=self.backend)

    def _assert_chunk(
        self,
        chunk: ToolChunk,
        state: str,
        text: str,
        metadata: dict | None = None,
    ) -> None:
        """Compare the response except for generated IDs and creation time."""
        self.assertEqual(
            chunk.model_dump(
                exclude={
                    "id": True,
                    "content": {"__all__": {"id", "created_at"}},
                },
            ),
            {
                "content": [
                    {"type": "text", "text": text, "finished_at": None},
                ],
                "state": state,
                "is_last": True,
                "metadata": metadata or {},
            },
        )

    def _assert_cached_file(self, lines: list[str], mtime: float) -> None:
        """Compare the complete cache against a single expected text file."""
        self.assertEqual(
            [
                entry.model_dump()
                for entry in self.state.tool_context.read_file_cache
            ],
            [
                {
                    "file_path": self.path,
                    "lines": lines,
                    "updated_at": mtime,
                    "bytes": len("".join(lines).encode("utf-8")) / 1024,
                },
            ],
        )

    def _assert_unknown_mtime_error(
        self,
        chunk: ToolChunk,
        operation: str,
    ) -> None:
        """Report why reading again cannot yet authorize a mutation."""
        action = {"Edit": "editing", "Write": "overwriting"}[operation]
        self._assert_chunk(
            chunk,
            "error",
            "Error: The backend could not report the file's modification "
            f"time. Once it is available, read the file again before {action} "
            "it.",
        )

    def _assert_updated_file(self, chunk: ToolChunk, operation: str) -> None:
        """Both mutations change only the first line of the existing file."""
        text = (
            f"Successfully replaced 1 occurrence in {self.path}"
            if operation == "Edit"
            else f"The file {self.path} has been written successfully "
            "(2 lines)."
        )
        self._assert_chunk(
            chunk,
            "running",
            text,
            {
                "diff": (
                    f"--- a/{self.path}\n+++ b/{self.path}\n"
                    "@@ -1,2 +1,2 @@\n-agent=old\n+agent=NEW\n human=NEW\n"
                ),
                "file_path": self.path,
                "occurrences": 1,
            },
        )

    async def _modify(self, operation: str) -> ToolChunk:
        """Apply either file mutation through its public tool interface."""
        if operation == "Edit":
            return await self.edit(
                file_path=self.path,
                old_string="agent=old",
                new_string="agent=NEW",
                _agent_state=self.state,
            )
        return await self.write(
            file_path=self.path,
            content="agent=NEW\nhuman=NEW\n",
            _agent_state=self.state,
        )

    async def test_unknown_timestamp_read_fetches_external_changes(
        self,
    ) -> None:
        """Repeated Read fetches remote bytes despite a stable host file."""
        with patch.object(self.backend, "stat_mtime", return_value=None):
            for value in ("old", "NEW"):
                await self.backend.write_file(
                    self.path,
                    f"agent=old\nhuman={value}\n".encode("utf-8"),
                )
                result = await self.read(
                    file_path=self.path,
                    _agent_state=self.state,
                )
                self._assert_chunk(
                    result,
                    "running",
                    f"     1\tagent=old\n     2\thuman={value}",
                )
                self.assertEqual(self.state.tool_context.read_file_cache, [])
        self.assertEqual(
            Path(self.path).read_bytes(),
            b"unrelated host contents\n",
        )

    async def test_unknown_timestamp_prevents_stale_edit_and_write(
        self,
    ) -> None:
        """Read cannot authorize overwriting a file whose mtime is unknown."""
        for operation in ("Edit", "Write"):
            with self.subTest(operation=operation):
                self.state = AgentState()
                await self.backend.write_file(
                    self.path,
                    b"agent=old\nhuman=old\n",
                )
                with patch.object(
                    self.backend,
                    "stat_mtime",
                    return_value=None,
                ):
                    await self.read(
                        file_path=self.path,
                        _agent_state=self.state,
                    )
                    await self.backend.write_file(
                        self.path,
                        b"agent=old\nhuman=NEW\n",
                    )
                    with patch.object(
                        self.backend,
                        "write_file",
                        wraps=self.backend.write_file,
                    ) as write_file:
                        result = await self._modify(operation)
                        self._assert_unknown_mtime_error(result, operation)
                        write_file.assert_not_awaited()
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=old\nhuman=NEW\n",
                )
                self.assertEqual(self.state.tool_context.read_file_cache, [])

    async def test_timestamp_recovery_requires_a_fresh_read(self) -> None:
        """Losing and recovering the same mtime requires a fresh Read."""
        for operation in ("Edit", "Write"):
            with self.subTest(operation=operation):
                self.state = AgentState()
                await self.backend.write_file(
                    self.path,
                    b"agent=old\nhuman=old\n",
                )
                self.backend._mtimes[self.path] = self.host_mtime
                await self.read(file_path=self.path, _agent_state=self.state)
                self._assert_cached_file(
                    ["agent=old\n", "human=old\n"],
                    self.host_mtime,
                )
                await self.backend.write_file(
                    self.path,
                    b"agent=old\nhuman=NEW\n",
                )
                with patch.object(
                    self.backend,
                    "stat_mtime",
                    return_value=None,
                ):
                    denied = await self._modify(operation)
                    self._assert_unknown_mtime_error(denied, operation)
                self.assertEqual(self.state.tool_context.read_file_cache, [])
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=old\nhuman=NEW\n",
                )
                self.backend._mtimes[self.path] = self.host_mtime
                denied = await self._modify(operation)
                message = (
                    "Error: To edit a file, you must first read it using "
                    "the Read tool."
                    if operation == "Edit"
                    else f"Error: File {self.path} exists but has not been "
                    "read yet. You must read the file first before writing "
                    "to it."
                )
                self._assert_chunk(denied, "error", message)
                await self.read(file_path=self.path, _agent_state=self.state)
                result = await self._modify(operation)
                self._assert_updated_file(result, operation)
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=NEW\nhuman=NEW\n",
                )
                self._assert_cached_file(
                    ["agent=NEW\n", "human=NEW\n"],
                    self.host_mtime + 0.001,
                )

    async def test_post_mutation_unknown_stat_discards_previous_cache(
        self,
    ) -> None:
        """Successful changes must not cache their bytes under host mtime."""
        for operation in ("Edit", "Write"):
            with self.subTest(operation=operation):
                self.state = AgentState()
                await self.backend.write_file(
                    self.path,
                    b"agent=old\nhuman=NEW\n",
                )
                self.backend._mtimes[self.path] = self.host_mtime
                await self.read(file_path=self.path, _agent_state=self.state)
                with patch.object(
                    self.backend,
                    "stat_mtime",
                    side_effect=[self.host_mtime, None],
                ):
                    result = await self._modify(operation)
                self._assert_updated_file(result, operation)
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=NEW\nhuman=NEW\n",
                )
                self.assertEqual(self.state.tool_context.read_file_cache, [])

    async def test_new_remote_file_write_needs_no_timestamp(self) -> None:
        """Creating a remote file works, even if a host namesake exists."""
        self.assertFalse(await self.backend.file_exists(self.path))
        with patch.object(self.backend, "stat_mtime", return_value=None):
            result = await self._modify("Write")
        self._assert_chunk(
            result,
            "running",
            f"The file {self.path} has been written successfully (2 lines).",
            {
                "diff": (
                    f"--- /dev/null\n+++ b/{self.path}\n"
                    "@@ -0,0 +1,2 @@\n+agent=NEW\n+human=NEW\n"
                ),
                "file_path": self.path,
                "occurrences": 1,
            },
        )
        self.assertEqual(
            await self.backend.read_file(self.path),
            b"agent=NEW\nhuman=NEW\n",
        )
        self.assertEqual(self.state.tool_context.read_file_cache, [])
        self.assertEqual(
            Path(self.path).read_bytes(),
            b"unrelated host contents\n",
        )
