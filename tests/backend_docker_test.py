# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DockerBackend`.

Validates that the three backend primitives (``exec_shell``,
``read_file``, ``write_file``) and the inherited shell-based filesystem
helpers behave correctly inside a real Docker container.

The suite is skipped unless:

* the Docker daemon is reachable (probed via ``docker info``), and
* the host is Linux — the project only validates the Docker backend on
  Ubuntu/Linux CI runners; spinning containers up on other hosts is
  out of scope for these tests.

A live container is obtained by initializing a :class:`DockerWorkspace`
and reusing its already-wired :class:`DockerBackend` (``ws._backend``),
which avoids duplicating the container bring-up logic here.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ExecResult
from agentscope.workspace import DockerWorkspace, DockerBackend
from agentscope.workspace._docker._make_dockerfile import CONTAINER_WORKDIR


# ── availability checks ────────────────────────────────────────────


def _docker_available() -> bool:
    """Return ``True`` iff the Docker daemon is reachable.

    Probes via the ``docker`` CLI (cheap, synchronous) so the result can
    gate the module at import time.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_IS_LINUX = sys.platform == "linux"
_DOCKER_OK = _IS_LINUX and _docker_available()
_SKIP_REASON = "Docker backend is only tested on Linux with a live daemon"


@unittest.skipUnless(_DOCKER_OK, _SKIP_REASON)
class TestDockerBackend(IsolatedAsyncioTestCase):
    """Test cases for ``DockerBackend`` against a live container."""

    async def asyncSetUp(self) -> None:
        """Start a workspace and reuse its wired backend.

        The ``workspace_id`` is randomised so concurrent / repeated runs
        do not collide on the deterministic container name.
        """
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
        )
        await self.workspace.initialize()
        self.backend = self.workspace._backend
        self.assertIsInstance(self.backend, DockerBackend)

    async def asyncTearDown(self) -> None:
        """Stop the container and drop the temp host dir."""
        try:
            await self.workspace.close()
        finally:
            self.temp_dir.cleanup()

    # ── exec ───────────────────────────────────────────────────────

    async def test_exec_returns_stdout(self) -> None:
        """A program's stdout/exit code are captured into ``ExecResult``."""
        result = await self.backend.exec_shell(["echo", "hello world"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "hello world")

    async def test_exec_nonzero_exit(self) -> None:
        """A non-zero exit is reported as a normal (non-raising) result."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo oops >&2; exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertIn("oops", result.stderr.decode())

    async def test_exec_argv_not_shell_split(self) -> None:
        """A single argv element with metacharacters reaches the program
        intact (no shell interposed by the primitive)."""
        tricky = "a b $(echo x) | ;"
        result = await self.backend.exec_shell(["echo", tricky])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\n"), tricky)

    async def test_exec_cwd_default_is_workdir(self) -> None:
        """With no explicit ``cwd`` the container workdir is used."""
        result = await self.backend.exec_shell(["pwd"])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), CONTAINER_WORKDIR)

    async def test_exec_timeout_returns_minus_one(self) -> None:
        """A command exceeding ``timeout`` reports the -1 sentinel."""
        result = await self.backend.exec_shell(
            ["sleep", "10"],
            timeout=0.5,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stderr, b"timed out")

    # ── file I/O ───────────────────────────────────────────────────

    async def test_write_then_read_roundtrip(self) -> None:
        """Bytes written into the container are read back verbatim."""
        path = f"{CONTAINER_WORKDIR}/roundtrip.txt"
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        path = f"{CONTAINER_WORKDIR}/a/b/c/file.txt"
        await self.backend.write_file(path, b"x")
        self.assertEqual(await self.backend.read_file(path), b"x")

    async def test_write_preserves_binary(self) -> None:
        """Raw bytes (incl. NULs and high bytes) survive the round-trip."""
        path = f"{CONTAINER_WORKDIR}/bin.dat"
        payload = b"a\r\nb\x00\xffc"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_read_missing_file_raises(self) -> None:
        """Reading a non-existent file raises ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{CONTAINER_WORKDIR}/nope.txt")

    # ── derived filesystem helpers (shell-based) ───────────────────

    async def test_file_exists_and_is_dir(self) -> None:
        """``file_exists`` / ``is_dir`` reflect the container filesystem."""
        path = f"{CONTAINER_WORKDIR}/f.txt"
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.file_exists(CONTAINER_WORKDIR))
        self.assertTrue(await self.backend.is_dir(CONTAINER_WORKDIR))
        self.assertFalse(await self.backend.is_dir(path))
        self.assertFalse(
            await self.backend.file_exists(
                f"{CONTAINER_WORKDIR}/missing",
            ),
        )

    async def test_list_dir(self) -> None:
        """Non-recursive ``list_dir`` returns immediate child base names."""
        base = f"{CONTAINER_WORKDIR}/listing"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"x")
        entries = await self.backend.list_dir(base)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_list_dir_recursive(self) -> None:
        """Recursive ``list_dir`` returns file paths underneath the root."""
        base = f"{CONTAINER_WORKDIR}/rec"
        await self.backend.write_file(f"{base}/top.txt", b"x")
        await self.backend.write_file(f"{base}/sub/nested.txt", b"x")
        entries = await self.backend.list_dir(base, recursive=True)
        basenames = sorted(e.rsplit("/", 1)[-1] for e in entries)
        self.assertEqual(basenames, ["nested.txt", "top.txt"])

    async def test_stat_mtime(self) -> None:
        """``stat_mtime`` returns a float for an existing path, None else."""
        path = f"{CONTAINER_WORKDIR}/stat.txt"
        await self.backend.write_file(path, b"x")
        mtime = await self.backend.stat_mtime(path)
        self.assertIsInstance(mtime, float)
        self.assertIsNone(
            await self.backend.stat_mtime(
                f"{CONTAINER_WORKDIR}/missing",
            ),
        )

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees; missing is a no-op."""
        path = f"{CONTAINER_WORKDIR}/to_delete.txt"
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(await self.backend.file_exists(path))

        tree = f"{CONTAINER_WORKDIR}/tree"
        await self.backend.write_file(f"{tree}/deep/f.txt", b"x")
        await self.backend.delete_path(tree)
        self.assertFalse(await self.backend.file_exists(tree))

        # Deleting a non-existent path must not raise.
        await self.backend.delete_path(f"{CONTAINER_WORKDIR}/missing")
