# -*- coding: utf-8 -*-
"""Unknown backend timestamps must never be replaced with host timestamps."""
# pylint: disable=protected-access
import os
from pathlib import Path
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiofiles.os
from builtin_file_cache_backend_test import _MemoryBackend

from agentscope.state import AgentState, ToolContext
from agentscope.tool import Edit, Read, ToolChunk, Write


class UnknownMtimeCacheTest(IsolatedAsyncioTestCase):
    """A host file and a backend file may have the same absolute path."""

    def setUp(self) -> None:
        # pylint: disable=consider-using-with
        directory = tempfile.TemporaryDirectory(prefix="agentscope-mtime-")
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "config.txt")
        Path(self.path).write_bytes(b"unrelated host contents\n")
        self.host_mtime = os.stat(self.path).st_mtime
        self.other = str(Path(directory.name) / "other.txt")
        self.context = ToolContext()
        self.backend = _MemoryBackend()
        # The shared memory fixture normally accepts POSIX sandbox paths.
        self.enterContext(
            patch.object(self.backend, "isabs", side_effect=os.path.isabs),
        )
        self.state = AgentState()
        self.read = Read(backend=self.backend)
        self.edit = Edit(backend=self.backend)
        self.write = Write(backend=self.backend)

    async def _seed_context(self) -> None:
        """Keep an unrelated entry to check that invalidation is local."""
        await self.context.cache_file(self.other, ["keep\n"], mtime=5.0)
        await self.context.cache_file(
            self.path,
            ["old\n"],
            mtime=self.host_mtime,
        )

    def _assert_only_unrelated_cache(self) -> None:
        """The unrelated path's contents and timestamp must be preserved."""
        self.assertEqual(
            [entry.model_dump() for entry in self.context.read_file_cache],
            [
                {
                    "file_path": self.other,
                    "lines": ["keep\n"],
                    "updated_at": 5.0,
                    "bytes": 5 / 1024,
                },
            ],
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

    async def test_explicit_unknown_get_invalidates_without_host_stat(
        self,
    ) -> None:
        """A matching host timestamp cannot validate remote cached lines."""
        await self._seed_context()
        with patch(
            "agentscope.state._state.aiofiles.os.path.getmtime",
            new=AsyncMock(return_value=self.host_mtime),
        ) as host_stat:
            self.assertIsNone(
                await self.context.get_cache(self.path, mtime=None),
            )
            host_stat.assert_not_awaited()
        self._assert_only_unrelated_cache()

    async def test_explicit_unknown_cache_removes_and_does_not_create(
        self,
    ) -> None:
        """Unknown replacement contents cannot retain an earlier cache."""
        await self._seed_context()
        with patch(
            "agentscope.state._state.aiofiles.os.path.getmtime",
            new=AsyncMock(return_value=self.host_mtime),
        ) as host_stat:
            for _ in range(2):
                await self.context.cache_file(self.path, ["new\n"], mtime=None)
                self._assert_only_unrelated_cache()
            host_stat.assert_not_awaited()

    async def test_omitted_timestamp_retains_host_fallback(self) -> None:
        """Legacy calls that omit mtime still validate against local stat."""
        with patch(
            "agentscope.state._state.aiofiles.os.path.getmtime",
            new=AsyncMock(wraps=aiofiles.os.path.getmtime),
        ) as host_stat:
            await self.context.cache_file(self.path, ["local\n"])
            entry = await self.context.get_cache(self.path)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.lines, ["local\n"])
            self.assertEqual(entry.updated_at, self.host_mtime)
            self.assertEqual(host_stat.await_count, 2)

    async def test_failed_omitted_stat_drops_only_matching_entry(self) -> None:
        """A failed legacy lookup cannot preserve previously cached bytes."""
        for operation in ("get", "cache"):
            with self.subTest(operation=operation):
                await self._seed_context()
                with patch(
                    "agentscope.state._state.aiofiles.os.path.getmtime",
                    new=AsyncMock(side_effect=OSError("stat unavailable")),
                ) as host_stat:
                    if operation == "get":
                        self.assertIsNone(
                            await self.context.get_cache(self.path),
                        )
                    else:
                        await self.context.cache_file(self.path, ["new\n"])
                    host_stat.assert_awaited_once_with(self.path)
                self._assert_only_unrelated_cache()

    async def test_explicit_timestamps_and_serialization_remain_supported(
        self,
    ) -> None:
        """Zero and negative backend timestamps need no host filesystem."""
        with patch(
            "agentscope.state._state.aiofiles.os.path.getmtime",
            new=AsyncMock(side_effect=AssertionError("unexpected host stat")),
        ) as host_stat:
            for mtime in (0.0, -0.125, 42.5):
                with self.subTest(mtime=mtime):
                    await self.context.cache_file(
                        self.path,
                        ["remote\n"],
                        mtime,
                    )
                    restored = ToolContext.model_validate(
                        self.context.model_dump(),
                    )
                    entry = await restored.get_cache(self.path, mtime)
                    self.assertIsNotNone(entry)
                    self.assertEqual(entry.updated_at, mtime)
                    self.assertEqual(entry.lines, ["remote\n"])
            host_stat.assert_not_awaited()

    async def test_unknown_timestamp_read_fetches_external_changes(
        self,
    ) -> None:
        """Repeated Read fetches remote bytes despite a stable host file."""
        await self.backend.write_file(self.path, b"agent=old\nhuman=old\n")
        with patch.object(self.backend, "stat_mtime", return_value=None):
            first = await self.read(
                file_path=self.path,
                _agent_state=self.state,
            )
            self.assertIn("human=old", first.content[0].text)
            await self.backend.write_file(self.path, b"agent=old\nhuman=NEW\n")
            second = await self.read(
                file_path=self.path,
                _agent_state=self.state,
            )
            self.assertIn("human=NEW", second.content[0].text)
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
                        self.assertEqual(result.state, "error")
                        write_file.assert_not_awaited()
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=old\nhuman=NEW\n",
                )
                self.assertEqual(self.state.tool_context.read_file_cache, [])

    async def test_timestamp_recovery_requires_a_fresh_read(self) -> None:
        """A successful stat alone must not resurrect a discarded read."""
        for operation in ("Edit", "Write"):
            with self.subTest(operation=operation):
                self.state = AgentState()
                await self.backend.write_file(
                    self.path,
                    b"agent=old\nhuman=NEW\n",
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
                # Match the host clock so a bogus host-backed entry would pass.
                self.backend._mtimes[self.path] = self.host_mtime
                denied = await self._modify(operation)
                self.assertEqual(denied.state, "error")
                await self.read(file_path=self.path, _agent_state=self.state)
                result = await self._modify(operation)
                self.assertEqual(result.state, "running")
                self.assertEqual(
                    await self.backend.read_file(self.path),
                    b"agent=NEW\nhuman=NEW\n",
                )
                self.assertEqual(
                    len(self.state.tool_context.read_file_cache),
                    1,
                )

    async def test_lost_timestamp_invalidates_an_existing_remote_read(
        self,
    ) -> None:
        """An unavailable backend stat invalidates a once-valid read."""
        await self.backend.write_file(self.path, b"agent=old\nhuman=old\n")
        self.backend._mtimes[self.path] = self.host_mtime
        await self.read(file_path=self.path, _agent_state=self.state)
        await self.backend.write_file(self.path, b"agent=old\nhuman=NEW\n")
        with patch.object(self.backend, "stat_mtime", return_value=None):
            result = await self._modify("Edit")
        self.assertEqual(result.state, "error")
        self.assertEqual(
            await self.backend.read_file(self.path),
            b"agent=old\nhuman=NEW\n",
        )
        self.assertEqual(self.state.tool_context.read_file_cache, [])

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
                self.assertEqual(result.state, "running")
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
        self.assertEqual(result.state, "running")
        self.assertEqual(
            await self.backend.read_file(self.path),
            b"agent=NEW\nhuman=NEW\n",
        )
        self.assertEqual(self.state.tool_context.read_file_cache, [])
        self.assertEqual(
            Path(self.path).read_bytes(),
            b"unrelated host contents\n",
        )
