# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerBackend`.

Runs against a real Apple Container via the ``container`` CLI.
Requires ``container`` CLI installed and ``container system start``
running.
"""

import shutil
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ExecResult
from agentscope.workspace import AppleContainerBackend

_CONTAINER_CLI = shutil.which("container")
_RUN_REASON = "container CLI not found — install Apple Container first"


@unittest.skipUnless(_CONTAINER_CLI, _RUN_REASON)
@unittest.skipUnless(
    sys.platform == "darwin",
    "Apple Container requires macOS",
)
class TestAppleContainerBackend(IsolatedAsyncioTestCase):
    """Test cases against a real Apple Container."""

    WORKDIR = "/workspace"

    @classmethod
    def setUpClass(cls) -> None:
        """Create a test container once for all tests."""
        import asyncio

        asyncio.run(cls._setup_container())

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the test container."""
        import asyncio

        asyncio.run(cls._teardown_container())

    @classmethod
    async def _setup_container(cls) -> None:
        """Create a container for testing."""
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "container",
            "run",
            "-d",
            "--name",
            "as_test_backend",
            "python:3.11-slim",
            "sleep",
            "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create test container: "
                f"{stderr.decode(errors='replace')}",
            )
        cls.container_id = stdout.decode().strip() or "as_test_backend"

    @classmethod
    async def _teardown_container(cls) -> None:
        """Remove the test container."""
        import asyncio

        await asyncio.create_subprocess_exec(
            "container",
            "stop",
            "as_test_backend",
        )
        await asyncio.create_subprocess_exec(
            "container",
            "rm",
            "-f",
            "as_test_backend",
        )

    def setUp(self) -> None:
        """Create a backend instance for each test."""
        self.backend = AppleContainerBackend(
            container_id="as_test_backend",
            workdir=self.WORKDIR,
        )

    # ── getcwd ─────────────────────────────────────────────────────

    async def test_getcwd_returns_workdir(self) -> None:
        """``getcwd`` returns the cached workdir."""
        self.assertEqual(await self.backend.getcwd(), self.WORKDIR)

    # ── exec_shell ─────────────────────────────────────────────────

    async def test_exec_simple_command(self) -> None:
        """A simple command runs successfully."""
        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.strip(), b"hello")

    async def test_exec_with_custom_cwd(self) -> None:
        """Custom ``cwd`` changes the working directory."""
        result = await self.backend.exec_shell(["pwd"], cwd="/tmp")
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.strip(), b"/tmp")

    async def test_exec_nonzero_exit(self) -> None:
        """Non-zero exit code is reported normally."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "exit 4"],
        )
        self.assertEqual(result.exit_code, 4)

    async def test_exec_timeout(self) -> None:
        """Timeout returns exit_code -1."""
        result = await self.backend.exec_shell(
            ["sleep", "10"],
            timeout=0.5,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"timed out", result.stderr)

    async def test_exec_stderr_captured(self) -> None:
        """Stderr is captured."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo err >&2"],
        )
        self.assertTrue(result.ok())
        self.assertIn(b"err", result.stderr)

    # ── read_file ──────────────────────────────────────────────────

    async def test_read_write_roundtrip(self) -> None:
        """Bytes written are read back verbatim."""
        path = f"{self.WORKDIR}/roundtrip.txt"
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_read_file_not_found(self) -> None:
        """Reading a missing file raises ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{self.WORKDIR}/missing.txt")

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        path = f"{self.WORKDIR}/a/b/c/file.txt"
        await self.backend.write_file(path, b"x")
        self.assertEqual(await self.backend.read_file(path), b"x")

    # ── derived filesystem helpers ─────────────────────────────────

    async def test_file_exists_and_is_dir(self) -> None:
        """``file_exists`` / ``is_dir`` work correctly."""
        path = f"{self.WORKDIR}/f.txt"
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.is_dir(self.WORKDIR))
        self.assertFalse(await self.backend.is_dir(path))
        self.assertFalse(
            await self.backend.file_exists(f"{self.WORKDIR}/missing"),
        )

    async def test_list_dir(self) -> None:
        """Non-recursive ``list_dir`` returns immediate children."""
        base = f"{self.WORKDIR}/listing"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"x")
        entries = await self.backend.list_dir(base)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees."""
        path = f"{self.WORKDIR}/to_delete.txt"
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(await self.backend.file_exists(path))

        tree = f"{self.WORKDIR}/tree"
        await self.backend.write_file(f"{tree}/deep/f.txt", b"x")
        await self.backend.delete_path(tree)
        self.assertFalse(await self.backend.file_exists(tree))
        # Deleting a non-existent path must not raise.
        await self.backend.delete_path(f"{self.WORKDIR}/missing")
