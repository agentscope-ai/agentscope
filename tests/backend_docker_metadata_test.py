# -*- coding: utf-8 -*-
"""Docker overwrites preserve ordinary file permissions and ownership."""
import asyncio
import io
import stat
import tarfile
from types import SimpleNamespace
from unittest import skipIf
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.tool import ExecResult
from agentscope.workspace import DockerBackend

try:
    from aiodocker.exceptions import DockerError
except ImportError:
    DockerError = None


def _result(stdout: bytes = b"", code: int = 0) -> ExecResult:
    """Build the result of the container's metadata command."""
    return ExecResult(exit_code=code, stdout=stdout, stderr=b"")


def _archive(
    name: str = "script.sh",
    kind: bytes = tarfile.REGTYPE,
    mode: int = 0o6751,
) -> tarfile.TarFile:
    """Build a Docker-style archive, including a tempting directory child."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        root = tarfile.TarInfo(name)
        root.type, root.mode, root.uid, root.gid = kind, mode, 1234, 2345
        if kind == tarfile.SYMTYPE:
            root.linkname = "private-target"
        archive.addfile(root)
        if kind == tarfile.DIRTYPE:
            child = tarfile.TarInfo(f"{name}/child")
            child.mode, child.uid, child.gid = 0o700, 3456, 4567
            archive.addfile(child)
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode="r")


@skipIf(DockerError is None, "Requires the workspace-docker extra")
class DockerWriteMetadataTest(IsolatedAsyncioTestCase):
    """Inspect the uploaded tar through the public write_file operation."""

    def setUp(self) -> None:
        """Use a mock Docker API; no daemon or container image is required."""
        self.container = SimpleNamespace(
            get_archive=AsyncMock(side_effect=DockerError(404, "not found")),
            put_archive=AsyncMock(),
        )
        self.backend = DockerBackend(self.container, workdir="/workspace")
        self.stat_result: ExecResult | BaseException = _result(code=127)

        async def execute(command: list[str], **_kwargs: object) -> ExecResult:
            if command[0] == "stat":
                if isinstance(self.stat_result, BaseException):
                    raise self.stat_result
                return self.stat_result
            return _result()

        self.shell = self.enterContext(
            patch.object(self.backend, "exec_shell", side_effect=execute),
        )

    def _assert_upload(
        self,
        path: str = "/data/script.sh",
        data: bytes = b"replacement",
        mode: int = 0o644,
        uid: int = 0,
        gid: int = 0,
    ) -> None:
        """Check metadata and exact bytes in the single uploaded member."""
        self.container.put_archive.assert_awaited_once()
        parent, payload = self.container.put_archive.await_args.args
        self.assertEqual(parent, path.rpartition("/")[0] or "/")
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as archive:
            members = archive.getmembers()
            self.assertEqual(len(members), 1)
            member = members[0]
            self.assertEqual(
                (member.name, member.mode, member.uid, member.gid),
                (path.rsplit("/", 1)[-1], mode, uid, gid),
            )
            self.assertTrue(member.isfile())
            self.assertEqual(member.size, len(data))
            self.assertEqual(archive.extractfile(member).read(), data)

    async def test_stat_preserves_permissions_and_numeric_ownership(
        self,
    ) -> None:
        """Executable/private files keep rwx bits; special bits are cleared."""
        for mode in (0o755, 0o600, 0o640, 0o400, 0o000, 0o6751, 0o1755):
            with self.subTest(mode=oct(mode)):
                self.stat_result = _result(
                    f"{stat.S_IFREG | mode:x} 1234 2345\n".encode(),
                )
                self.container.put_archive.reset_mock()
                await self.backend.write_file(
                    "/data/script.sh",
                    b"replacement",
                )
                self._assert_upload(mode=mode & 0o777, uid=1234, gid=2345)
                self.container.get_archive.assert_not_awaited()

    async def test_stat_does_not_inherit_nonregular_metadata(self) -> None:
        """Symlinks, directories, and special files use new-file metadata."""
        for kind in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO):
            with self.subTest(kind=kind):
                self.stat_result = _result(f"{kind | 0o777:x} 12 34".encode())
                self.container.put_archive.reset_mock()
                await self.backend.write_file(
                    "/data/script.sh",
                    b"replacement",
                )
                self._assert_upload()
                self.container.get_archive.assert_not_awaited()

    async def test_stat_failures_fall_back_to_archive_metadata(self) -> None:
        """Unavailable commands and malformed metadata must not reset mode."""
        failures = [
            _result(code=127),
            _result(code=-1),
            DockerError(500, "exec unavailable"),
            _result(),
            _result(b"not-hex 1234 2345"),
            _result(b"81ed not-uid 2345"),
            _result(b"81ed -1 2345"),
            _result(b"81ed 1234 -1"),
            _result(b"1ed 1234 2345"),
            _result(b"81ed 1234"),
            _result(b"81ed 1234 2345 unexpected"),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                self.stat_result = failure
                archive = _archive()
                self.container.get_archive.side_effect = None
                self.container.get_archive.return_value = archive
                self.container.get_archive.reset_mock()
                self.container.put_archive.reset_mock()
                await self.backend.write_file(
                    "/data/script.sh",
                    b"replacement",
                )
                self._assert_upload(mode=0o751, uid=1234, gid=2345)
                self.container.get_archive.assert_awaited_once_with(
                    "/data/script.sh",
                )
                self.assertTrue(archive.closed)

    async def test_fallback_uses_root_type_without_following_links(
        self,
    ) -> None:
        """Do not inherit metadata from directory children or link targets."""
        for kind in (tarfile.SYMTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                archive = _archive(kind=kind)
                self.container.get_archive.side_effect = None
                self.container.get_archive.return_value = archive
                self.container.put_archive.reset_mock()
                await self.backend.write_file(
                    "/data/script.sh",
                    b"replacement",
                )
                self._assert_upload()
                self.assertTrue(archive.closed)

    async def test_unusable_archive_stops_before_upload_and_closes(
        self,
    ) -> None:
        """An empty or mismatched root cannot authorize metadata loss."""
        empty = io.BytesIO()
        with tarfile.open(fileobj=empty, mode="w"):
            pass
        empty.seek(0)
        # Let write_file own closure so the assertions detect resource leaks.
        # pylint: disable=consider-using-with
        archives = [tarfile.open(fileobj=empty), _archive("other-file")]
        for case, archive in enumerate(archives):
            with self.subTest(case=case):
                self.container.get_archive.side_effect = None
                self.container.get_archive.return_value = archive
                with self.assertRaises(OSError):
                    await self.backend.write_file(
                        "/data/script.sh",
                        b"replacement",
                    )
                self.container.put_archive.assert_not_awaited()
                self.assertTrue(archive.closed)

    async def test_missing_destination_keeps_new_file_defaults(self) -> None:
        """Only Docker's 404 denotes a new path with ordinary defaults."""
        await self.backend.write_file("/data/script.sh", b"replacement")
        self.container.get_archive.assert_awaited_once_with("/data/script.sh")
        self._assert_upload()

    async def test_archive_api_and_decode_errors_prevent_upload(self) -> None:
        """Docker failures and corrupt archives do not mean a new file."""
        for error in (DockerError(500, "daemon failed"), tarfile.ReadError()):
            with self.subTest(error=type(error).__name__):
                self.container.get_archive.side_effect = error
                with self.assertRaises(type(error)):
                    await self.backend.write_file(
                        "/data/script.sh",
                        b"replacement",
                    )
                self.container.put_archive.assert_not_awaited()

    async def test_cancellation_is_not_converted_into_an_overwrite(
        self,
    ) -> None:
        """Cancellation at either metadata stage stops before put_archive."""
        self.stat_result = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.backend.write_file("/data/script.sh", b"replacement")
        self.container.get_archive.assert_not_awaited()
        self.container.put_archive.assert_not_awaited()
        self.stat_result = _result(code=127)
        self.container.get_archive.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.backend.write_file("/data/script.sh", b"replacement")
        self.container.put_archive.assert_not_awaited()

    async def test_metadata_path_matches_archive_destination(self) -> None:
        """Relative paths use Docker's archive root, not the exec workdir."""
        payload = b"\x00\xff\r\nreplacement"
        for path, destination in (
            ("script.sh", "/script.sh"),
            ("sub/script.sh", "/sub/script.sh"),
            ("/data/link/../script.sh", "/data/script.sh"),
            ("/data/a b;$(touch x).bin", "/data/a b;$(touch x).bin"),
        ):
            with self.subTest(path=path):
                self.stat_result = _result(b"8180 1234 2345")
                self.shell.reset_mock()
                self.container.put_archive.reset_mock()
                await self.backend.write_file(path, payload)
                self.shell.assert_any_await(
                    ["stat", "-c", "%f %u %g", "--", destination],
                )
                self._assert_upload(path, payload, 0o600, 1234, 2345)

    async def test_relative_archive_lookup_uses_upload_root(self) -> None:
        """Archive fallback reads the same absolute path as the stat probe."""
        archive = _archive()
        self.container.get_archive.side_effect = None
        self.container.get_archive.return_value = archive
        await self.backend.write_file("script.sh", b"replacement")
        self.container.get_archive.assert_awaited_once_with("/script.sh")
        self._assert_upload("script.sh", mode=0o751, uid=1234, gid=2345)
        self.assertTrue(archive.closed)
