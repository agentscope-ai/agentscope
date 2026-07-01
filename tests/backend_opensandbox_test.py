# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-class-docstring
# pylint: disable=missing-function-docstring,wrong-import-position
# pylint: disable=redefined-outer-name,reimported
"""Test cases for :class:`OpenSandboxBackend`.

Most tests run against mocked SDK objects so CI does not need a live
OpenSandbox service. A smaller credential-gated live suite reuses
``OpenSandboxWorkspace`` to bring up a real sandbox and then applies the
shared remote backend contracts from ``workspace_remote_contract_test``.

The local import stubs below keep this module importable when optional
workspace dependencies (for example E2B or MCP packages) are absent; the
actual OpenSandbox implementation is still imported from ``src``.
"""

from __future__ import annotations

import importlib
import os
import posixpath
import shlex
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


def _install_workspace_public_exports(
    agentscope_workspace: types.ModuleType,
    src_root: Path,
) -> None:
    """Expose public workspace names without eager optional deps."""

    agentscope_workspace.__all__ = [
        "WorkspaceBase",
        "E2BBackend",
        "E2BWorkspace",
        "OpenSandboxBackend",
        "OpenSandboxWorkspace",
    ]

    def _ensure_e2b_package() -> None:
        if "agentscope.workspace._e2b" not in sys.modules:
            e2b_pkg = types.ModuleType("agentscope.workspace._e2b")
            e2b_pkg.__path__ = [str(src_root / "workspace" / "_e2b")]
            sys.modules["agentscope.workspace._e2b"] = e2b_pkg

    def __getattr__(name: str) -> object:
        if name == "E2BBackend":
            _ensure_e2b_package()
            from agentscope.workspace._e2b._e2b_backend import E2BBackend

            agentscope_workspace.E2BBackend = E2BBackend
            return E2BBackend
        if name == "E2BWorkspace":
            _ensure_e2b_package()
            try:
                from agentscope.workspace._e2b._e2b_workspace import (
                    E2BWorkspace,
                )
            except ImportError:
                E2BWorkspace = type("E2BWorkspace", (), {})

            agentscope_workspace.E2BWorkspace = E2BWorkspace
            return E2BWorkspace
        if name == "OpenSandboxBackend":
            module = importlib.import_module(
                "agentscope.workspace._opensandbox._opensandbox_backend",
            )
            OpenSandboxBackend = module.OpenSandboxBackend

            agentscope_workspace.OpenSandboxBackend = OpenSandboxBackend
            return OpenSandboxBackend
        if name == "OpenSandboxWorkspace":
            module = importlib.import_module(
                "agentscope.workspace._opensandbox._opensandbox_workspace",
            )
            OpenSandboxWorkspace = module.OpenSandboxWorkspace

            agentscope_workspace.OpenSandboxWorkspace = OpenSandboxWorkspace
            return OpenSandboxWorkspace
        raise AttributeError(name)

    agentscope_workspace.__getattr__ = __getattr__


def _install_import_stubs() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "agentscope"

    if "agentscope.tool" not in sys.modules:
        agentscope_tool = types.ModuleType("agentscope.tool")
        agentscope_tool.__path__ = [str(src_root / "tool")]

        class _BackendBase:
            _path_module = posixpath

            def join_path(self, path: str, *paths: str) -> str:
                return self._path_module.join(path, *paths)

            def dirname(self, path: str) -> str:
                return self._path_module.dirname(path)

            def basename(self, path: str) -> str:
                return self._path_module.basename(path)

            def isabs(self, path: str) -> bool:
                return self._path_module.isabs(path)

            def normpath(self, path: str) -> str:
                return self._path_module.normpath(path)

            def abspath(self, path: str, *, cwd: str) -> str:
                if self._path_module.isabs(path):
                    return self._path_module.normpath(path)
                return self._path_module.normpath(
                    self._path_module.join(cwd, path),
                )

            async def file_exists(self, path: str) -> bool:
                result = await self.exec_shell(["test", "-e", path])
                return result.ok()

            async def is_dir(self, path: str) -> bool:
                result = await self.exec_shell(["test", "-d", path])
                return result.ok()

            async def list_dir(
                self,
                path: str,
                *,
                recursive: bool = False,
            ) -> list[str]:
                if recursive:
                    command = ["find", path, "-type", "f", "-print0"]
                else:
                    command = [
                        "find",
                        path,
                        "-mindepth",
                        "1",
                        "-maxdepth",
                        "1",
                        "-printf",
                        "%f\\0",
                    ]
                result = await self.exec_shell(command)
                if not result.ok():
                    return []
                return [
                    part.decode("utf-8", errors="surrogateescape")
                    for part in result.stdout.split(b"\0")
                    if part
                ]

            async def stat_mtime(self, path: str) -> float | None:
                quoted = shlex.quote(path)
                script = (
                    f"stat -c %Y {quoted} 2>/dev/null || "
                    f"stat -f %m {quoted} 2>/dev/null"
                )
                result = await self.exec_shell(["sh", "-c", script])
                if not result.ok():
                    return None
                try:
                    return float(
                        result.stdout.decode(
                            "utf-8",
                            errors="replace",
                        ).strip(),
                    )
                except ValueError:
                    return None

            async def delete_path(self, path: str) -> None:
                await self.exec_shell(["rm", "-rf", path])

        class _ExecResult:
            def __init__(
                self,
                exit_code: int,
                stdout: bytes,
                stderr: bytes,
            ) -> None:
                self.exit_code = exit_code
                self.stdout = stdout
                self.stderr = stderr

            def ok(self) -> bool:
                return self.exit_code == 0

        class _BuiltinTool:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.args = args
                self.kwargs = kwargs

        class Bash(_BuiltinTool):
            name = "Bash"

        class Edit(_BuiltinTool):
            name = "Edit"

        class Glob(_BuiltinTool):
            name = "Glob"

        class Grep(_BuiltinTool):
            name = "Grep"

        class Read(_BuiltinTool):
            name = "Read"

        class Write(_BuiltinTool):
            name = "Write"

        agentscope_tool.BackendBase = _BackendBase
        agentscope_tool.ExecResult = _ExecResult
        agentscope_tool.ToolBase = object
        agentscope_tool.ToolChunk = object
        agentscope_tool.Bash = Bash
        agentscope_tool.Edit = Edit
        agentscope_tool.Glob = Glob
        agentscope_tool.Grep = Grep
        agentscope_tool.Read = Read
        agentscope_tool.Write = Write
        sys.modules["agentscope.tool"] = agentscope_tool

    if "agentscope.workspace" not in sys.modules:
        agentscope_workspace = types.ModuleType("agentscope.workspace")
        agentscope_workspace.__path__ = [str(src_root / "workspace")]

        class _WorkspaceBase:
            def __init__(self, workspace_id: str | None = None) -> None:
                self.workspace_id = workspace_id

        agentscope_workspace.WorkspaceBase = _WorkspaceBase
        _install_workspace_public_exports(agentscope_workspace, src_root)
        sys.modules["agentscope.workspace"] = agentscope_workspace

    if "agentscope.workspace._opensandbox" not in sys.modules:
        opensandbox_pkg = types.ModuleType("agentscope.workspace._opensandbox")
        opensandbox_pkg.__path__ = [
            str(src_root / "workspace" / "_opensandbox"),
        ]
        sys.modules["agentscope.workspace._opensandbox"] = opensandbox_pkg


_install_import_stubs()

from agentscope.tool import ExecResult  # noqa: E402
from tests.workspace_remote_contract_test import (  # noqa: E402
    RemoteBackendContractMixin,
)

OpenSandboxBackend = importlib.import_module(
    "agentscope.workspace._opensandbox._opensandbox_backend",
).OpenSandboxBackend

_OPEN_SANDBOX_API_KEY = os.getenv("OPEN_SANDBOX_API_KEY", "")
_OPEN_SANDBOX_DOMAIN = os.getenv("OPEN_SANDBOX_DOMAIN", "")
_OPEN_SANDBOX_SKIP = (
    "OPEN_SANDBOX_API_KEY and OPEN_SANDBOX_DOMAIN environment variables "
    "are not set"
)


class _FakeCommands:
    def __init__(self) -> None:
        self.run = AsyncMock()


class _FakeFiles:
    def __init__(self) -> None:
        self.read_bytes = AsyncMock()
        self.read_file = AsyncMock()
        self.write_files = AsyncMock()


class _FakeSandbox:
    def __init__(self) -> None:
        self.commands = _FakeCommands()
        self.files = _FakeFiles()


class TestOpenSandboxBackend(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sandbox = _FakeSandbox()
        self.backend = OpenSandboxBackend(self.sandbox, workdir="/workspace")

    async def test_path_helpers_use_posix_paths(self) -> None:
        self.assertEqual(
            self.backend.join_path("/workspace", "a", "b.txt"),
            "/workspace/a/b.txt",
        )

    async def test_exec_returns_stdout(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="hello\n",
            stderr="",
        )
        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout, b"hello\n")
        self.assertEqual(result.stderr, b"")
        self.sandbox.commands.run.assert_awaited_once()
        opts = self.sandbox.commands.run.await_args.kwargs["opts"]
        self.assertEqual(opts.working_directory, "/workspace")

    async def test_exec_reads_sdk_logs_shape(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=3,
            logs=SimpleNamespace(
                stdout=[
                    SimpleNamespace(text="hello "),
                    SimpleNamespace(text="world\n"),
                ],
                stderr=[SimpleNamespace(text="oops\n")],
            ),
        )

        result = await self.backend.exec_shell(["sh", "-c", "demo"])

        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.stdout, b"hello world\n")
        self.assertEqual(result.stderr, b"oops\n")

    async def test_exec_restores_line_separators_between_log_entries(
        self,
    ) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            logs=SimpleNamespace(
                stdout=[
                    SimpleNamespace(text="/workspace"),
                    SimpleNamespace(text="Python 3.11.15"),
                ],
                stderr=[],
            ),
        )

        result = await self.backend.exec_shell(
            ["sh", "-c", "pwd && python -V"],
        )

        self.assertEqual(result.stdout, b"/workspace\nPython 3.11.15")

    async def test_exec_preserves_argv_quoting(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="a b | ;\n",
            stderr="",
        )
        await self.backend.exec_shell(["echo", "a b | ;"])
        command_line = self.sandbox.commands.run.await_args.args[0]
        self.assertEqual(command_line, "echo 'a b | ;'")

    async def test_exec_nonzero_is_result(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=4,
            stdout="",
            stderr="oops",
        )
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo oops >&2; exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(result.stderr, b"oops")

    async def test_exec_transport_error_returns_minus_one(self) -> None:
        self.sandbox.commands.run.side_effect = RuntimeError("network down")
        result = await self.backend.exec_shell(["echo", "x"])
        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"network down", result.stderr)

    async def test_read_file_returns_bytes_from_bytes(self) -> None:
        self.sandbox.files.read_bytes.return_value = b"abc"
        self.assertEqual(
            await self.backend.read_file("/workspace/a.txt"),
            b"abc",
        )

    async def test_read_file_maps_http_404_to_file_not_found(self) -> None:
        error = RuntimeError("client error")
        error.response = SimpleNamespace(status_code=404)
        self.sandbox.files.read_bytes.side_effect = error

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/workspace/missing.txt")

    async def test_read_file_maps_wrapped_http_404_to_file_not_found(
        self,
    ) -> None:
        cause = RuntimeError("client error")
        cause.response = SimpleNamespace(status_code=404)
        error = RuntimeError("wrapped")
        error.__cause__ = cause
        self.sandbox.files.read_bytes.side_effect = error

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/workspace/missing.txt")

    async def test_read_file_keeps_non_404_errors(self) -> None:
        error = RuntimeError("server error")
        error.response = SimpleNamespace(status_code=500)
        self.sandbox.files.read_bytes.side_effect = error

        with self.assertRaises(RuntimeError):
            await self.backend.read_file("/workspace/a.txt")

    async def test_read_file_keeps_not_found_message_fallback(self) -> None:
        self.sandbox.files.read_bytes.side_effect = RuntimeError(
            "file not found",
        )

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/workspace/missing.txt")

    async def test_write_file_creates_parent_and_writes_binary_entry(
        self,
    ) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="",
            stderr="",
        )
        await self.backend.write_file("/workspace/a/b.bin", b"a\r\nb\x00\xff")
        mkdir_cmd = self.sandbox.commands.run.await_args_list[0].args[0]
        self.assertEqual(mkdir_cmd, "mkdir -p /workspace/a")
        entry = self.sandbox.files.write_files.await_args.args[0][0]
        self.assertEqual(entry.path, "/workspace/a/b.bin")
        self.assertEqual(entry.data, b"a\r\nb\x00\xff")
        self.assertEqual(entry.mode, 0o644)

    async def test_inherited_helpers_use_exec(self) -> None:
        self.sandbox.commands.run.side_effect = [
            SimpleNamespace(exit_code=0, stdout="", stderr=""),
            SimpleNamespace(exit_code=0, stdout="", stderr=""),
            SimpleNamespace(exit_code=0, stdout="a.txt\0b.txt\0", stderr=""),
            SimpleNamespace(exit_code=0, stdout="1234567890", stderr=""),
            SimpleNamespace(exit_code=0, stdout="", stderr=""),
        ]
        self.assertTrue(await self.backend.file_exists("/workspace/a.txt"))
        self.assertTrue(await self.backend.is_dir("/workspace"))
        self.assertEqual(
            await self.backend.list_dir("/workspace"),
            ["a.txt", "b.txt"],
        )
        self.assertEqual(
            await self.backend.stat_mtime("/workspace/a.txt"),
            1234567890.0,
        )
        await self.backend.delete_path("/workspace/a.txt")

    async def test_inherited_is_dir_uses_exec(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="",
            stderr="",
        )

        self.assertTrue(await self.backend.is_dir("/workspace/dir"))
        command_line = self.sandbox.commands.run.await_args.args[0]
        self.assertEqual(command_line, "test -d /workspace/dir")

    async def test_inherited_list_dir_uses_exec_and_parses_entries(
        self,
    ) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="a.txt\0b.txt\0",
            stderr="",
        )

        self.assertEqual(
            await self.backend.list_dir("/workspace/dir"),
            ["a.txt", "b.txt"],
        )
        command_line = self.sandbox.commands.run.await_args.args[0]
        self.assertEqual(
            command_line,
            "find /workspace/dir -mindepth 1 -maxdepth 1 -printf '%f\\0'",
        )

    async def test_inherited_stat_mtime_uses_exec(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="1712345678\n",
            stderr="",
        )

        self.assertEqual(
            await self.backend.stat_mtime("/workspace/a.txt"),
            1712345678.0,
        )
        command_line = self.sandbox.commands.run.await_args.args[0]
        self.assertIn("stat -c %Y /workspace/a.txt", command_line)

    async def test_inherited_delete_path_uses_exec(self) -> None:
        self.sandbox.commands.run.return_value = SimpleNamespace(
            exit_code=0,
            stdout="",
            stderr="",
        )

        await self.backend.delete_path("/workspace/a.txt")

        command_line = self.sandbox.commands.run.await_args.args[0]
        self.assertEqual(command_line, "rm -rf /workspace/a.txt")


class TestOpenSandboxImportIsolation(unittest.TestCase):
    def test_tool_stub_exposes_builtins_for_shared_workspace_base(
        self,
    ) -> None:
        import agentscope.tool as agentscope_tool

        for name in ("Bash", "Edit", "Glob", "Grep", "Read", "Write"):
            self.assertTrue(hasattr(agentscope_tool, name), name)

    def test_e2b_backend_test_imports_after_opensandbox_stub(self) -> None:
        sys.modules.pop("tests.backend_e2b_test", None)

        with patch.dict(os.environ, {"E2B_API_KEY": "dummy"}):
            importlib.import_module("tests.backend_e2b_test")


@unittest.skipUnless(
    _OPEN_SANDBOX_API_KEY and _OPEN_SANDBOX_DOMAIN,
    _OPEN_SANDBOX_SKIP,
)
class TestOpenSandboxBackendLive(
    RemoteBackendContractMixin,
    IsolatedAsyncioTestCase,
):
    async def asyncSetUp(self) -> None:
        from agentscope.workspace._opensandbox._bootstrap import (
            SANDBOX_WORKDIR,
        )
        from agentscope.workspace._opensandbox._opensandbox_workspace import (
            OpenSandboxWorkspace,
        )

        self.workspace = OpenSandboxWorkspace(
            api_key=_OPEN_SANDBOX_API_KEY,
            domain=_OPEN_SANDBOX_DOMAIN,
        )
        await self.workspace.initialize()
        self.backend = self.workspace._backend
        self.workdir = SANDBOX_WORKDIR

    async def asyncTearDown(self) -> None:
        await self.workspace.close()


if __name__ == "__main__":
    unittest.main()
