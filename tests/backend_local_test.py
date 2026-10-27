# -*- coding: utf-8 -*-
"""Test cases for :class:`LocalBackend` and the backend helpers.

Exercises the three abstract primitives (``exec_shell``, ``read_file``,
``write_file``) plus the derived filesystem helpers (``file_exists``,
``is_dir``, ``list_dir``, ``scandir``, ``read_stream``, ``stat_mtime``,
``delete_path``) of the host-local backend, and the module-level
``normalize_newlines`` helper.

``LocalBackend`` is designed to run on every platform (it spawns
programs from an argv list without a shell and implements the
filesystem helpers with native ``os.*`` calls), so the bulk of this
module runs on Windows too.  Only the handful of cases that genuinely
rely on a POSIX shell / POSIX-only utilities are skipped on Windows.
"""

import os
import sys
import tempfile
import unittest
from typing import AsyncIterator
from unittest import mock
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ExecResult, LocalBackend
from agentscope.tool._builtin._backend import _normalize_newlines

_IS_WINDOWS = sys.platform == "win32"


class TestNormalizeNewlines(unittest.TestCase):
    """Unit tests for the ``normalize_newlines`` helper (pure, no I/O)."""

    def test_crlf_collapsed_to_lf(self) -> None:
        """Windows ``\\r\\n`` is collapsed to a single ``\\n``."""
        self.assertEqual(
            _normalize_newlines("a\r\nb\r\nc"),
            "a\nb\nc",
        )

    def test_lone_cr_collapsed_to_lf(self) -> None:
        """Classic-Mac lone ``\\r`` is collapsed to ``\\n``."""
        self.assertEqual(_normalize_newlines("a\rb\rc"), "a\nb\nc")

    def test_mixed_endings(self) -> None:
        """A mix of ``\\r\\n``, ``\\r`` and ``\\n`` normalizes uniformly."""
        self.assertEqual(
            _normalize_newlines("a\r\nb\rc\nd"),
            "a\nb\nc\nd",
        )

    def test_plain_lf_unchanged(self) -> None:
        """Text already using ``\\n`` is returned unchanged."""
        self.assertEqual(_normalize_newlines("a\nb\nc"), "a\nb\nc")

    def test_no_double_collapse(self) -> None:
        """``\\r\\n`` becomes exactly one ``\\n`` (not two)."""
        self.assertEqual(_normalize_newlines("a\r\n\r\nb"), "a\n\nb")


class TestLocalBackendExec(IsolatedAsyncioTestCase):
    """Test cases for ``LocalBackend.exec_shell``."""

    async def asyncSetUp(self) -> None:
        """Build a fresh backend per test."""
        self.backend = LocalBackend()

    async def test_exec_returns_stdout(self) -> None:
        """A program's stdout/exit code are captured into ``ExecResult``.

        Uses the current interpreter so the test is portable across
        platforms (no reliance on ``echo`` / shell builtins).
        """
        result = await self.backend.exec_shell(
            [sys.executable, "-c", "print('hello world')"],
        )
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.decode().strip(), "hello world")
        self.assertEqual(result.stderr, b"")

    async def test_exec_captures_stderr_and_exit_code(self) -> None:
        """A non-zero exit and stderr bytes are reported faithfully."""
        result = await self.backend.exec_shell(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('boom'); sys.exit(3)",
            ],
        )
        self.assertFalse(result.ok())
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.stderr.decode().strip(), "boom")

    async def test_exec_argv_not_split_by_shell(self) -> None:
        """Arguments are passed verbatim (no shell word-splitting/globbing).

        A single argument containing spaces and shell metacharacters must
        reach the program intact, proving no shell is interposed.
        """
        tricky = "a b $(echo x) | & ; '\""
        result = await self.backend.exec_shell(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", tricky],
        )
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\r\n"), tricky)

    async def test_exec_cwd_is_respected(self) -> None:
        """``cwd`` sets the subprocess working directory."""
        with tempfile.TemporaryDirectory() as tmp:
            result = await self.backend.exec_shell(
                [sys.executable, "-c", "import os; print(os.getcwd())"],
                cwd=tmp,
            )
            self.assertTrue(result.ok())
            self.assertEqual(
                os.path.realpath(result.stdout.decode().strip()),
                os.path.realpath(tmp),
            )

    async def test_missing_executable_returns_127(self) -> None:
        """An unspawnable executable yields exit code 127 (not an exception).

        Mirrors a shell's "command not found" so callers see a normal
        non-zero ``ExecResult``.
        """
        result = await self.backend.exec_shell(
            ["this_executable_does_not_exist_xyz", "--nope"],
        )
        self.assertEqual(result.exit_code, 127)
        self.assertEqual(result.stdout, b"")
        self.assertNotEqual(result.stderr, b"")

    async def test_timeout_returns_minus_one(self) -> None:
        """A command exceeding ``timeout`` is killed and reports -1.

        The sentinel ``ExecResult(exit_code=-1, stderr=b"timed out")`` is
        what Grep/Glob check for, so it is asserted exactly.
        """
        result = await self.backend.exec_shell(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.2,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stderr, b"timed out")

    async def test_unsupported_event_loop_raises_runtime_error(self) -> None:
        """Test NotImplementedError from subprocess creation is wrapped."""
        with mock.patch(
            "agentscope.tool._builtin._backend.asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await self.backend.exec_shell([sys.executable, "-c", "pass"])
        self.assertIsInstance(ctx.exception.__cause__, NotImplementedError)


class TestLocalBackendFileIO(IsolatedAsyncioTestCase):
    """Test cases for ``read_file`` / ``write_file`` round-trips."""

    async def asyncSetUp(self) -> None:
        """Build a backend and a temp dir per test."""
        # pylint: disable=consider-using-with
        self.backend = LocalBackend()
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Drop the temp dir."""
        self.temp_dir.cleanup()

    async def test_write_then_read_roundtrip(self) -> None:
        """Bytes written are read back verbatim."""
        path = os.path.join(self.temp_dir.name, "file.txt")
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        path = os.path.join(self.temp_dir.name, "a", "b", "c", "file.txt")
        await self.backend.write_file(path, b"x")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(await self.backend.read_file(path), b"x")

    async def test_write_preserves_binary_and_crlf(self) -> None:
        """Raw bytes (incl. ``\\r\\n`` and NULs) survive the round-trip.

        The backend deals in raw bytes; newline normalization happens
        later in the text layer, never here.
        """
        path = os.path.join(self.temp_dir.name, "bin.dat")
        payload = b"a\r\nb\x00\xffc"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_write_stream_writes_chunks_in_order(self) -> None:
        """Streamed chunks land in the destination in order."""

        async def chunks() -> AsyncIterator[bytes]:
            yield b"hello\n"
            yield b"world\n"

        path = os.path.join(self.temp_dir.name, "stream.txt")
        await self.backend.write_stream(path, chunks())
        self.assertEqual(await self.backend.read_file(path), b"hello\nworld\n")

    async def test_failed_write_stream_preserves_existing_file(self) -> None:
        """A stream that fails mid-way must not touch the old bytes."""

        async def failing() -> AsyncIterator[bytes]:
            yield b"partial replacement"
            raise OSError("upload source failed")

        path = os.path.join(self.temp_dir.name, "doc.txt")
        await self.backend.write_file(path, b"original complete document")

        with self.assertRaisesRegex(OSError, "upload source failed"):
            await self.backend.write_stream(path, failing())

        self.assertEqual(
            await self.backend.read_file(path),
            b"original complete document",
        )
        self.assertEqual(os.listdir(self.temp_dir.name), ["doc.txt"])

    async def test_failed_write_stream_leaves_no_new_file(self) -> None:
        """A failed first write must not publish a partial file."""

        async def failing() -> AsyncIterator[bytes]:
            yield b"partial replacement"
            raise OSError("upload source failed")

        path = os.path.join(self.temp_dir.name, "new.txt")
        with self.assertRaisesRegex(OSError, "upload source failed"):
            await self.backend.write_stream(path, failing())

        self.assertFalse(os.path.exists(path))
        self.assertEqual(os.listdir(self.temp_dir.name), [])

    async def test_failed_publication_preserves_existing_file(self) -> None:
        """A failure while publishing leaves the destination alone."""
        path = os.path.join(self.temp_dir.name, "doc.txt")
        await self.backend.write_file(path, b"original complete document")

        with mock.patch(
            "agentscope.tool._builtin._backend.os.replace",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                await self.backend.write_file(path, b"new content")

        self.assertEqual(
            await self.backend.read_file(path),
            b"original complete document",
        )
        self.assertEqual(os.listdir(self.temp_dir.name), ["doc.txt"])

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions only")
    async def test_write_keeps_existing_file_mode(self) -> None:
        """Publishing does not silently widen an existing file's mode."""
        path = os.path.join(self.temp_dir.name, "secret.txt")
        await self.backend.write_file(path, b"one")
        os.chmod(path, 0o640)

        await self.backend.write_file(path, b"two")

        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)

    @unittest.skipIf(sys.platform == "win32", "symlinks need extra privileges")
    async def test_write_follows_symlinked_destination(self) -> None:
        """Writing through a symlink updates the target, not the link."""
        target = os.path.join(self.temp_dir.name, "target.txt")
        link = os.path.join(self.temp_dir.name, "link.txt")
        await self.backend.write_file(target, b"one")
        os.symlink(target, link)

        await self.backend.write_file(link, b"two")

        self.assertTrue(os.path.islink(link))
        self.assertEqual(await self.backend.read_file(target), b"two")

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows refuses to replace an open file",
    )
    async def test_write_retries_a_transient_permission_error(self) -> None:
        """A reader holding the file open does not fail the write."""
        path = os.path.join(self.temp_dir.name, "retry.txt")
        await self.backend.write_file(path, b"one")

        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src: str, dst: str) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise PermissionError("file is open")
            real_replace(src, dst)

        with mock.patch(
            "agentscope.tool._builtin._backend.os.replace",
            side_effect=flaky_replace,
        ):
            await self.backend.write_file(path, b"two")

        self.assertEqual(calls["count"], 2)
        self.assertEqual(await self.backend.read_file(path), b"two")

    async def test_read_missing_file_raises(self) -> None:
        """Reading a non-existent file raises ``FileNotFoundError``."""
        path = os.path.join(self.temp_dir.name, "nope.txt")
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(path)


class TestLocalBackendFilesystemHelpers(IsolatedAsyncioTestCase):
    """Test cases for the derived filesystem helpers (native ``os.*``)."""

    async def asyncSetUp(self) -> None:
        """Build a backend and a temp dir per test."""
        # pylint: disable=consider-using-with
        self.backend = LocalBackend()
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Drop the temp dir."""
        self.temp_dir.cleanup()

    async def test_file_exists(self) -> None:
        """``file_exists`` is True for files and dirs, False otherwise."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.file_exists(self.temp_dir.name))
        self.assertFalse(
            await self.backend.file_exists(
                os.path.join(self.temp_dir.name, "missing"),
            ),
        )

    async def test_is_dir(self) -> None:
        """``is_dir`` distinguishes directories from files."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.is_dir(self.temp_dir.name))
        self.assertFalse(await self.backend.is_dir(path))
        self.assertFalse(
            await self.backend.is_dir(
                os.path.join(self.temp_dir.name, "missing"),
            ),
        )

    async def test_list_dir_shallow(self) -> None:
        """Non-recursive ``list_dir`` returns immediate child base names."""
        for name in ("a.txt", "b.txt"):
            await self.backend.write_file(
                os.path.join(self.temp_dir.name, name),
                b"x",
            )
        os.makedirs(os.path.join(self.temp_dir.name, "sub"))
        entries = await self.backend.list_dir(self.temp_dir.name)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt", "sub"])

    async def test_list_dir_recursive(self) -> None:
        """Recursive ``list_dir`` returns file paths underneath the root."""
        await self.backend.write_file(
            os.path.join(self.temp_dir.name, "top.txt"),
            b"x",
        )
        await self.backend.write_file(
            os.path.join(self.temp_dir.name, "sub", "nested.txt"),
            b"x",
        )
        entries = await self.backend.list_dir(
            self.temp_dir.name,
            recursive=True,
        )
        basenames = sorted(os.path.basename(e) for e in entries)
        self.assertEqual(basenames, ["nested.txt", "top.txt"])

    async def test_stat_mtime(self) -> None:
        """``stat_mtime`` returns a float for an existing path, None else."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"x")
        mtime = await self.backend.stat_mtime(path)
        self.assertIsInstance(mtime, float)
        self.assertIsNone(
            await self.backend.stat_mtime(
                os.path.join(self.temp_dir.name, "missing"),
            ),
        )

    async def test_scandir_returns_metadata_per_entry(self) -> None:
        """``scandir`` carries type, size and mtime with each name."""
        await self.backend.write_file(
            os.path.join(self.temp_dir.name, "f.txt"),
            b"payload",
        )
        os.makedirs(os.path.join(self.temp_dir.name, "d"))
        entries = {
            e.name: e
            for e in await self.backend.scandir(
                self.temp_dir.name,
            )
        }
        self.assertEqual(sorted(entries), ["d", "f.txt"])
        self.assertFalse(entries["f.txt"].is_dir)
        self.assertEqual(entries["f.txt"].size_bytes, 7)
        self.assertIsNotNone(entries["f.txt"].mtime)
        self.assertTrue(entries["d"].is_dir)
        # A directory's own size is noise; the contract pins it to None.
        self.assertIsNone(entries["d"].size_bytes)

    async def test_stat_reports_file_and_directory(self) -> None:
        """``stat`` answers type, size and mtime in one call."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"payload")
        entry = await self.backend.stat(path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.name, "f.txt")
        self.assertFalse(entry.is_dir)
        self.assertEqual(entry.size_bytes, 7)
        self.assertIsNotNone(entry.mtime)

        directory = await self.backend.stat(self.temp_dir.name)
        self.assertTrue(directory.is_dir)
        self.assertIsNone(directory.size_bytes)

    async def test_stat_missing_path_returns_none(self) -> None:
        """A path with nothing behind it stats to None, not an error."""
        self.assertIsNone(
            await self.backend.stat(
                os.path.join(self.temp_dir.name, "missing"),
            ),
        )

    async def test_scandir_missing_dir_returns_empty(self) -> None:
        """An unlistable path yields no entries rather than raising."""
        self.assertEqual(
            await self.backend.scandir(
                os.path.join(self.temp_dir.name, "missing"),
            ),
            [],
        )

    async def test_read_stream_chunks_without_buffering(self) -> None:
        """``read_stream`` yields the file in ``chunk_size`` pieces."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"abcdefg")
        chunks = [c async for c in self.backend.read_stream(path, 3)]
        self.assertEqual(chunks, [b"abc", b"def", b"g"])

    async def test_read_stream_empty_file_yields_nothing(self) -> None:
        """An empty file produces no chunks, not one empty chunk."""
        path = os.path.join(self.temp_dir.name, "empty.txt")
        await self.backend.write_file(path, b"")
        self.assertEqual([c async for c in self.backend.read_stream(path)], [])

    async def test_delete_path_file(self) -> None:
        """``delete_path`` removes a single file."""
        path = os.path.join(self.temp_dir.name, "f.txt")
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(os.path.exists(path))

    async def test_delete_path_tree(self) -> None:
        """``delete_path`` removes a directory tree recursively."""
        nested = os.path.join(self.temp_dir.name, "d", "e")
        os.makedirs(nested)
        await self.backend.write_file(
            os.path.join(nested, "f.txt"),
            b"x",
        )
        target = os.path.join(self.temp_dir.name, "d")
        await self.backend.delete_path(target)
        self.assertFalse(os.path.exists(target))

    async def test_delete_path_missing_is_noop(self) -> None:
        """Deleting a non-existent path is a silent no-op (like rm -rf)."""
        # Must not raise.
        await self.backend.delete_path(
            os.path.join(self.temp_dir.name, "missing"),
        )

    async def test_delete_path_symlinks_preserves_targets(self) -> None:
        """Remove links, including dangling ones, without touching targets."""
        for kind in ("file", "directory", "broken-file", "broken-directory"):
            with self.subTest(kind=kind):
                target = os.path.join(self.temp_dir.name, kind + "-target")
                link = os.path.join(self.temp_dir.name, kind + "-link")
                payload_path = target
                if kind == "directory":
                    os.makedirs(target)
                    payload_path = os.path.join(target, "child.txt")
                if not kind.startswith("broken"):
                    await self.backend.write_file(payload_path, b"preserve me")
                try:
                    os.symlink(
                        target,
                        link,
                        target_is_directory="directory" in kind,
                    )
                except OSError as exc:
                    if _IS_WINDOWS and exc.winerror == 1314:
                        self.skipTest("Creating symlinks requires permission")
                    raise

                await self.backend.delete_path(link)
                self.assertFalse(os.path.lexists(link))
                if not kind.startswith("broken"):
                    self.assertEqual(
                        await self.backend.read_file(payload_path),
                        b"preserve me",
                    )
                else:
                    self.assertFalse(os.path.exists(target))
                await self.backend.delete_path(link)


@unittest.skipIf(
    _IS_WINDOWS,
    "POSIX shell (/bin/sh) is not available on Windows",
)
class TestLocalBackendShellWrapping(IsolatedAsyncioTestCase):
    """Cases that explicitly use ``sh -c`` (POSIX-only, skipped on Windows).

    The backend primitive never invokes a shell itself; these tests cover
    the documented escape hatch where a *caller* wraps a command line as
    ``["/bin/sh", "-c", line]`` to use pipes / redirects / ``&&``.
    """

    async def asyncSetUp(self) -> None:
        """Build a backend per test."""
        self.backend = LocalBackend()

    async def test_sh_c_pipeline(self) -> None:
        """A piped command line runs when wrapped in ``sh -c``."""
        result = await self.backend.exec_shell(
            ["/bin/sh", "-c", "printf 'a\\nb\\na\\n' | sort | uniq"],
        )
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().split(), ["a", "b"])

    async def test_sh_c_and_chain(self) -> None:
        """``&&`` chaining works through ``sh -c``."""
        result = await self.backend.exec_shell(
            ["/bin/sh", "-c", "true && echo chained"],
        )
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "chained")


if __name__ == "__main__":
    unittest.main()
