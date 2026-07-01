# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-class-docstring
# pylint: disable=missing-function-docstring,wrong-import-position
# pylint: disable=redefined-outer-name,reimported,unused-argument
# pylint: disable=too-many-statements,too-many-public-methods
"""Test cases for :class:`OpenSandboxWorkspace`.

The suite mirrors the E2B workspace tests at two levels:

* mocked SDK tests cover lifecycle decisions, bootstrap, gateway
  wiring, MCP persistence, skills, reset, and the workspace manager
  without requiring a running OpenSandbox service;
* live tests are gated by ``OPEN_SANDBOX_API_KEY`` and
  ``OPEN_SANDBOX_DOMAIN`` and apply the same remote contracts used for
  E2B-style backends.

The import stubs keep optional dependency availability from deciding
whether the OpenSandbox unit tests can be collected.
"""

from __future__ import annotations

import importlib
import os
import posixpath
import shlex
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
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


def _install_workspace_manager_public_exports(
    workspace_manager_pkg: types.ModuleType,
    src_root: Path,
) -> None:
    """Expose E2B manager imports when this test owns the manager stub."""

    workspace_manager_pkg.__all__ = [
        "E2BWorkspaceManager",
        "OpenSandboxWorkspaceManager",
    ]

    def __getattr__(name: str) -> object:
        if name == "E2BWorkspaceManager":
            try:
                module = importlib.import_module(
                    "agentscope.app.workspace_manager."
                    "_e2b_workspace_manager",
                )
                E2BWorkspaceManager = module.E2BWorkspaceManager
            except ImportError:
                E2BWorkspaceManager = type("E2BWorkspaceManager", (), {})

            workspace_manager_pkg.E2BWorkspaceManager = E2BWorkspaceManager
            return E2BWorkspaceManager
        if name == "OpenSandboxWorkspaceManager":
            module = importlib.import_module(
                "agentscope.app.workspace_manager."
                "_opensandbox_workspace_manager",
            )
            OpenSandboxWorkspaceManager = module.OpenSandboxWorkspaceManager

            workspace_manager_pkg.OpenSandboxWorkspaceManager = (
                OpenSandboxWorkspaceManager
            )
            return OpenSandboxWorkspaceManager
        raise AttributeError(name)

    workspace_manager_pkg.__getattr__ = __getattr__


def _install_import_stubs() -> None:
    """Stub optional deps needed to import the workspace package in tests."""
    src_root = Path(__file__).resolve().parents[1] / "src" / "agentscope"

    if (
        "httpx" not in sys.modules
        and importlib.util.find_spec("httpx") is None
    ):
        httpx = types.ModuleType("httpx")

        class _AsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.args = args
                self.kwargs = kwargs

            async def __aenter__(self) -> "_AsyncClient":
                return self

            async def __aexit__(
                self,
                exc_type: Any,
                exc: Any,
                tb: Any,
            ) -> None:
                return None

            async def aclose(self) -> None:
                return None

        class _Response:
            status_code = 200
            text = ""

            def json(self) -> dict[str, Any]:
                return {}

            def raise_for_status(self) -> None:
                return None

        class _HTTPStatusError(Exception):
            pass

        httpx.AsyncClient = _AsyncClient
        httpx.Response = _Response
        httpx.HTTPStatusError = _HTTPStatusError
        sys.modules["httpx"] = httpx

    if "docstring_parser" not in sys.modules:
        docstring_parser = types.ModuleType("docstring_parser")

        class _ParsedDocstring:
            def __init__(self) -> None:
                self.short_description = None
                self.long_description = None
                self.params = []

        def _parse(_docstring: str) -> _ParsedDocstring:
            return _ParsedDocstring()

        docstring_parser.parse = _parse
        sys.modules["docstring_parser"] = docstring_parser

    if "mcp" not in sys.modules:
        mcp = types.ModuleType("mcp")
        mcp_types = types.ModuleType("mcp.types")

        class _Tool:
            def __init__(
                self,
                name: str = "",
                description: str | None = None,
                inputSchema: dict | None = None,
                annotations: object | None = None,
            ) -> None:
                self.name = name
                self.description = description
                self.inputSchema = inputSchema
                self.annotations = annotations

            @classmethod
            def model_validate(cls, data: Any) -> Any:
                if isinstance(data, cls):
                    return data
                return cls(**data)

        mcp_types.Tool = _Tool
        mcp.types = mcp_types
        sys.modules["mcp"] = mcp
        sys.modules["mcp.types"] = mcp_types

    if "agentscope.mcp" not in sys.modules:
        agentscope_mcp = types.ModuleType("agentscope.mcp")

        @dataclass
        class _StdioMCPConfig:
            command: str
            args: list[str]

            def model_dump(self, mode: str = "json") -> dict:
                return {
                    "type": "stdio",
                    "command": self.command,
                    "args": list(self.args),
                }

        @dataclass
        class _MCPClient:
            name: str
            is_stateful: bool
            mcp_config: _StdioMCPConfig

            def model_dump(self, mode: str = "json") -> dict:
                return {
                    "name": self.name,
                    "is_stateful": self.is_stateful,
                    "mcp_config": self.mcp_config.model_dump(mode=mode),
                }

            @classmethod
            def model_validate(cls, data: Any) -> Any:
                if isinstance(data, cls):
                    return data
                cfg = data["mcp_config"]
                if isinstance(cfg, _StdioMCPConfig):
                    mcp_config = cfg
                else:
                    mcp_config = _StdioMCPConfig(
                        command=cfg["command"],
                        args=list(cfg.get("args", [])),
                    )
                return cls(
                    name=data["name"],
                    is_stateful=data.get("is_stateful", True),
                    mcp_config=mcp_config,
                )

        agentscope_mcp.MCPClient = _MCPClient
        agentscope_mcp.StdioMCPConfig = _StdioMCPConfig
        sys.modules["agentscope.mcp"] = agentscope_mcp

    if "agentscope.skill" not in sys.modules:
        agentscope_skill = types.ModuleType("agentscope.skill")

        @dataclass
        class _Skill:
            name: str
            description: str
            dir: str
            markdown: str
            updated_at: float

        agentscope_skill.Skill = _Skill
        sys.modules["agentscope.skill"] = agentscope_skill

    if "agentscope.message" not in sys.modules:
        agentscope_message = types.ModuleType("agentscope.message")

        @dataclass
        class _Base64Source:
            data: str
            media_type: str

        @dataclass
        class _URLSource:
            url: str
            media_type: str

        @dataclass
        class _DataBlock:
            source: object
            name: str | None = None
            id: str = "data-1"

        @dataclass
        class _TextBlock:
            text: str
            id: str = "text-1"

        class _Msg:
            def __init__(self, name: str, role: str, content: Any) -> None:
                self.name = name
                self.role = role
                self.content = content

            def model_dump_json(self) -> str:
                if isinstance(self.content, str):
                    content = self.content
                else:
                    content = [
                        getattr(block, "__dict__", block)
                        for block in self.content
                    ]
                return '{"name":"%s","role":"%s","content":%s}' % (
                    self.name,
                    self.role,
                    (
                        f'"{content}"'
                        if isinstance(content, str)
                        else str(content).replace("'", '"')
                    ),
                )

        @dataclass
        class _ToolResultBlock:
            id: str
            name: str
            output: object

        agentscope_message.Base64Source = _Base64Source
        agentscope_message.URLSource = _URLSource
        agentscope_message.DataBlock = _DataBlock
        agentscope_message.TextBlock = _TextBlock
        agentscope_message.Msg = _Msg
        agentscope_message.ToolResultBlock = _ToolResultBlock
        agentscope_message.ToolResultState = SimpleNamespace(ERROR="error")
        sys.modules["agentscope.message"] = agentscope_message

    if "agentscope.permission" not in sys.modules:
        agentscope_permission = types.ModuleType("agentscope.permission")

        class _PermissionBehavior:
            ALLOW = "allow"
            ASK = "ask"

        class _PermissionDecision:
            def __init__(
                self,
                behavior: Any = None,
                message: str = "",
            ) -> None:
                self.behavior = behavior
                self.message = message

        agentscope_permission.PermissionBehavior = _PermissionBehavior
        agentscope_permission.PermissionDecision = _PermissionDecision
        sys.modules["agentscope.permission"] = agentscope_permission

    if "agentscope.tool" not in sys.modules:
        agentscope_tool = types.ModuleType("agentscope.tool")
        agentscope_tool.__path__ = [str(src_root / "tool")]

        class _BackendBase:
            def join_path(self, path: str, *paths: str) -> str:
                return posixpath.join(path, *paths)

            def dirname(self, path: str) -> str:
                return posixpath.dirname(path)

            def basename(self, path: str) -> str:
                return posixpath.basename(path)

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
            def __init__(self, *args: Any, **kwargs: Any) -> None:
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

    if "agentscope.tool._builtin" not in sys.modules:
        builtin_tools = types.ModuleType("agentscope.tool._builtin")
        builtin_tools.__path__ = [str(src_root / "tool" / "_builtin")]

        class _BuiltinPackageTool:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.kwargs = kwargs

        class _BuiltinBash(_BuiltinPackageTool):
            name = "Bash"

        class _BuiltinEdit(_BuiltinPackageTool):
            name = "Edit"

        class _BuiltinGlob(_BuiltinPackageTool):
            name = "Glob"

        class _BuiltinGrep(_BuiltinPackageTool):
            name = "Grep"

        class _BuiltinRead(_BuiltinPackageTool):
            name = "Read"

        class _BuiltinWrite(_BuiltinPackageTool):
            name = "Write"

        builtin_tools.Bash = _BuiltinBash
        builtin_tools.Edit = _BuiltinEdit
        builtin_tools.Glob = _BuiltinGlob
        builtin_tools.Grep = _BuiltinGrep
        builtin_tools.Read = _BuiltinRead
        builtin_tools.Write = _BuiltinWrite
        sys.modules["agentscope.tool._builtin"] = builtin_tools

    if "frontmatter" not in sys.modules:
        frontmatter = types.ModuleType("frontmatter")

        class _Doc(dict):
            def __init__(self, data: dict, content: str) -> None:
                super().__init__(data)
                self.content = content

        def _loads(text: str) -> _Doc:
            if not text.startswith("---"):
                return _Doc({}, text)
            _sep, rest = text.split("---", 1)
            header, content = rest.split("---", 1)
            data = {}
            for line in header.strip().splitlines():
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip('"')
            return _Doc(data, content.strip())

        frontmatter.loads = _loads
        sys.modules["frontmatter"] = frontmatter

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

    if "agentscope.app" not in sys.modules:
        app_pkg = types.ModuleType("agentscope.app")
        app_pkg.__path__ = [str(src_root / "app")]
        sys.modules["agentscope.app"] = app_pkg

    if "agentscope.app.workspace_manager" not in sys.modules:
        workspace_manager_pkg = types.ModuleType(
            "agentscope.app.workspace_manager",
        )
        workspace_manager_pkg.__path__ = [
            str(src_root / "app" / "workspace_manager"),
        ]
        _install_workspace_manager_public_exports(
            workspace_manager_pkg,
            src_root,
        )
        sys.modules["agentscope.app.workspace_manager"] = workspace_manager_pkg


_install_import_stubs()

from agentscope.workspace._opensandbox._bootstrap import (  # noqa: E402
    GATEWAY_SCRIPT,
    METADATA_WORKSPACE_ID_KEY,
)
from agentscope.workspace._sandboxed_base import (  # noqa: E402
    SandboxedWorkspaceBase,
)
from tests.workspace_remote_contract_test import (  # noqa: E402
    RemoteWorkspaceContractMixin,
    RemoteWorkspaceManagerContractMixin,
)

OpenSandboxWorkspace = importlib.import_module(
    "agentscope.workspace._opensandbox._opensandbox_workspace",
).OpenSandboxWorkspace


class TestOpenSandboxWorkspaceStructure(unittest.TestCase):
    def test_uses_shared_sandboxed_workspace_base(self) -> None:
        self.assertTrue(
            issubclass(OpenSandboxWorkspace, SandboxedWorkspaceBase),
        )
        for name in (
            "initialize",
            "close",
            "reset",
            "list_tools",
            "list_mcps",
            "add_mcp",
            "remove_mcp",
            "list_skills",
            "add_skill",
            "remove_skill",
            "offload_context",
            "offload_tool_result",
            "_write_gateway_config",
            "_start_gateway_process",
            "_wait_for_gateway",
        ):
            self.assertIs(
                getattr(OpenSandboxWorkspace, name),
                getattr(SandboxedWorkspaceBase, name),
                name,
            )


def _opensandbox_manager_cls() -> Any:
    """Return the OpenSandbox workspace manager class after stubs load."""
    return importlib.import_module(
        "agentscope.app.workspace_manager._opensandbox_workspace_manager",
    ).OpenSandboxWorkspaceManager


_OPEN_SANDBOX_API_KEY = os.getenv("OPEN_SANDBOX_API_KEY", "")
_OPEN_SANDBOX_DOMAIN = os.getenv("OPEN_SANDBOX_DOMAIN", "")
_OPEN_SANDBOX_SKIP = (
    "OPEN_SANDBOX_API_KEY and OPEN_SANDBOX_DOMAIN environment variables "
    "are not set"
)


class _FakeFiles:
    def __init__(self, marker_exists: bool = True) -> None:
        if marker_exists:
            self.read_file = AsyncMock(return_value=b"gateway")
        else:
            self.read_file = AsyncMock(side_effect=FileNotFoundError())
        self.write_files = AsyncMock()


class _FakeCommands:
    def __init__(self, marker_exists: bool = True) -> None:
        self.marker_exists = marker_exists
        self.run = AsyncMock(side_effect=self._run)

    async def _run(
        self,
        command: str,
        *args: Any,
        **kwargs: Any,
    ) -> SimpleNamespace:
        if command == f"test -e {GATEWAY_SCRIPT}":
            return SimpleNamespace(
                exit_code=0 if self.marker_exists else 1,
                stdout="",
                stderr="",
            )
        if command.startswith("test -e "):
            return SimpleNamespace(exit_code=1, stdout="", stderr="")
        return SimpleNamespace(exit_code=0, stdout="", stderr="")


class _FakeSandbox:
    def __init__(
        self,
        sandbox_id: str = "sbx_1",
        marker_exists: bool = True,
    ) -> None:
        self.id = sandbox_id
        self.files = _FakeFiles(marker_exists)
        self.commands = _FakeCommands(marker_exists)
        self.pause = AsyncMock()
        self.close = AsyncMock()
        self.is_running = AsyncMock(return_value=True)
        self.get_endpoint = AsyncMock(
            return_value=SimpleNamespace(
                endpoint="http://127.0.0.1:5600",
                headers={},
            ),
        )


class _BackendDouble:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.deleted: list[str] = []
        self.exec_calls: list[list[str]] = []

    def join_path(self, path: str, *paths: str) -> str:
        return posixpath.join(path, *paths)

    def dirname(self, path: str) -> str:
        return posixpath.dirname(path)

    def basename(self, path: str) -> str:
        return posixpath.basename(path)

    async def exec_shell(
        self,
        command: list[str],
        **_kwargs: Any,
    ) -> SimpleNamespace:
        self.exec_calls.append(command)
        return SimpleNamespace(
            exit_code=0,
            stdout=b"",
            stderr=b"",
            ok=lambda: True,
        )

    async def is_dir(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(item.startswith(prefix) for item in self.files)

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        prefix = path.rstrip("/") + "/"
        matches = [
            item
            for item in self.files
            if item.startswith(prefix) and item != path
        ]
        if recursive:
            return matches
        children = {item[len(prefix) :].split("/", 1)[0] for item in matches}
        return sorted(children)

    async def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    async def file_exists(self, path: str) -> bool:
        if path in self.files:
            return True
        prefix = path.rstrip("/") + "/"
        return any(item.startswith(prefix) for item in self.files)

    async def delete_path(self, path: str) -> None:
        self.deleted.append(path)
        prefix = path.rstrip("/") + "/"
        for item in list(self.files):
            if item == path or item.startswith(prefix):
                del self.files[item]


class _FakeConnectionConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeSandboxClass:
    create = AsyncMock()
    resume = AsyncMock()
    connect = AsyncMock()


class _FakeSandboxManager:
    create = AsyncMock()


class _FakeSandboxFilter:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _fake_opensandbox_modules() -> dict[str, types.ModuleType]:
    module = types.ModuleType("opensandbox")
    module.Sandbox = _FakeSandboxClass
    module.SandboxManager = _FakeSandboxManager

    config = types.ModuleType("opensandbox.config")
    connection = types.ModuleType("opensandbox.config.connection")
    connection.ConnectionConfig = _FakeConnectionConfig

    models = types.ModuleType("opensandbox.models")
    sandboxes = types.ModuleType("opensandbox.models.sandboxes")
    sandboxes.SandboxFilter = _FakeSandboxFilter

    return {
        "opensandbox": module,
        "opensandbox.config": config,
        "opensandbox.config.connection": connection,
        "opensandbox.models": models,
        "opensandbox.models.sandboxes": sandboxes,
    }


class TestOpenSandboxWorkspaceLifecycle(IsolatedAsyncioTestCase):
    async def test_attach_or_create_sandbox_passes_create_contract(
        self,
    ) -> None:
        sandbox = _FakeSandbox("sbx_created")
        _FakeSandboxClass.create = AsyncMock(return_value=sandbox)
        _FakeSandboxClass.resume = AsyncMock()
        _FakeSandboxClass.connect = AsyncMock()
        fake_opensandbox = _fake_opensandbox_modules()

        with patch.dict(sys.modules, fake_opensandbox), patch.object(
            OpenSandboxWorkspace,
            "_find_existing_sandbox",
            AsyncMock(return_value=None),
        ):
            ws = OpenSandboxWorkspace(
                workspace_id="wid",
                image="python:3.12",
                request_timeout_seconds=12.5,
                timeout_seconds=42,
                env={"A": "1"},
                sandbox_metadata={
                    METADATA_WORKSPACE_ID_KEY: "bad",
                    "x": "y",
                },
                resource={"cpu": "2"},
                entrypoint=["python", "-m", "app"],
                network_policy={"egress": "allow"},
            )

            await ws._attach_or_create_sandbox()

        _FakeSandboxClass.create.assert_awaited_once()
        create_kwargs = _FakeSandboxClass.create.await_args.kwargs
        self.assertEqual(create_kwargs["image"], "python:3.12")
        self.assertEqual(create_kwargs["env"], {"A": "1"})
        self.assertEqual(create_kwargs["resource"], {"cpu": "2"})
        self.assertEqual(create_kwargs["entrypoint"], ["python", "-m", "app"])
        self.assertEqual(create_kwargs["network_policy"], {"egress": "allow"})
        self.assertEqual(create_kwargs["timeout"], timedelta(seconds=42))
        self.assertEqual(create_kwargs["ready_timeout"], timedelta(seconds=42))
        self.assertEqual(
            create_kwargs["metadata"],
            {
                METADATA_WORKSPACE_ID_KEY: "wid",
                "x": "y",
            },
        )
        self.assertIs(ws._sandbox, sandbox)

        connection_config = create_kwargs["connection_config"]
        self.assertIsInstance(connection_config, _FakeConnectionConfig)
        self.assertEqual(connection_config.kwargs["protocol"], "http")
        self.assertEqual(
            connection_config.kwargs["request_timeout"],
            timedelta(seconds=12.5),
        )

    async def test_attach_or_create_sandbox_passes_resume_contract(
        self,
    ) -> None:
        sandbox = _FakeSandbox("sbx_existing")
        _FakeSandboxClass.create = AsyncMock()
        _FakeSandboxClass.resume = AsyncMock(return_value=sandbox)
        _FakeSandboxClass.connect = AsyncMock()
        fake_opensandbox = _fake_opensandbox_modules()
        existing = SimpleNamespace(
            id="sbx_existing",
            created_at=2,
            status=SimpleNamespace(state="Paused"),
        )

        with patch.dict(sys.modules, fake_opensandbox), patch.object(
            OpenSandboxWorkspace,
            "_find_existing_sandbox",
            AsyncMock(return_value=existing),
        ):
            ws = OpenSandboxWorkspace(
                workspace_id="wid",
                request_timeout_seconds=7.0,
                timeout_seconds=60,
                network_policy={"egress": "deny"},
            )

            await ws._attach_or_create_sandbox()

        _FakeSandboxClass.resume.assert_awaited_once()
        _FakeSandboxClass.connect.assert_not_awaited()
        resume_kwargs = _FakeSandboxClass.resume.await_args.kwargs
        self.assertEqual(resume_kwargs["sandbox_id"], "sbx_existing")
        self.assertEqual(
            resume_kwargs["resume_timeout"],
            timedelta(seconds=60),
        )
        self.assertNotIn("network_policy", resume_kwargs)
        self.assertIs(ws._sandbox, sandbox)

        connection_config = resume_kwargs["connection_config"]
        self.assertIsInstance(connection_config, _FakeConnectionConfig)
        self.assertEqual(
            connection_config.kwargs["request_timeout"],
            timedelta(seconds=7.0),
        )

    async def test_attach_or_create_sandbox_connects_running_sandbox(
        self,
    ) -> None:
        sandbox = _FakeSandbox("sbx_running")
        _FakeSandboxClass.create = AsyncMock()
        _FakeSandboxClass.resume = AsyncMock()
        _FakeSandboxClass.connect = AsyncMock(return_value=sandbox)
        fake_opensandbox = _fake_opensandbox_modules()
        existing = SimpleNamespace(
            id="sbx_running",
            created_at=2,
            status=SimpleNamespace(state="Running"),
        )

        with patch.dict(sys.modules, fake_opensandbox), patch.object(
            OpenSandboxWorkspace,
            "_find_existing_sandbox",
            AsyncMock(return_value=existing),
        ):
            ws = OpenSandboxWorkspace(
                workspace_id="wid",
                request_timeout_seconds=7.0,
                timeout_seconds=60,
                network_policy={"egress": "deny"},
            )

            await ws._attach_or_create_sandbox()

        _FakeSandboxClass.connect.assert_awaited_once()
        _FakeSandboxClass.resume.assert_not_awaited()
        connect_kwargs = _FakeSandboxClass.connect.await_args.kwargs
        self.assertEqual(connect_kwargs["sandbox_id"], "sbx_running")
        self.assertEqual(
            connect_kwargs["connect_timeout"],
            timedelta(seconds=60),
        )
        self.assertNotIn("network_policy", connect_kwargs)
        self.assertIs(ws._sandbox, sandbox)

        connection_config = connect_kwargs["connection_config"]
        self.assertIsInstance(connection_config, _FakeConnectionConfig)
        self.assertEqual(
            connection_config.kwargs["request_timeout"],
            timedelta(seconds=7.0),
        )

    async def test_find_existing_sandbox_filters_resumable_paged_infos(
        self,
    ) -> None:
        fake_opensandbox = _fake_opensandbox_modules()
        manager = SimpleNamespace(
            list_sandbox_infos=AsyncMock(),
            close=AsyncMock(),
        )
        older = SimpleNamespace(
            id="older",
            created_at=1,
            status=SimpleNamespace(state="Paused"),
        )
        newer = SimpleNamespace(
            id="newer",
            created_at=2,
            status=SimpleNamespace(state="Running"),
        )
        manager.list_sandbox_infos.return_value = SimpleNamespace(
            sandbox_infos=[older, newer],
        )
        _FakeSandboxManager.create = AsyncMock(return_value=manager)

        with patch.dict(sys.modules, fake_opensandbox):
            ws = OpenSandboxWorkspace(workspace_id="wid")
            found = await ws._find_existing_sandbox()

        self.assertIs(found, newer)
        _FakeSandboxManager.create.assert_awaited_once()
        sandbox_filter = manager.list_sandbox_infos.await_args.args[0]
        self.assertEqual(
            sandbox_filter.kwargs,
            {
                "states": ["RUNNING", "PAUSED"],
                "metadata": {METADATA_WORKSPACE_ID_KEY: "wid"},
            },
        )
        manager.close.assert_awaited_once()

    async def test_find_existing_sandbox_ignores_manager_close_failure(
        self,
    ) -> None:
        fake_opensandbox = _fake_opensandbox_modules()
        manager = SimpleNamespace(
            list_sandbox_infos=AsyncMock(
                return_value=SimpleNamespace(
                    sandbox_infos=[
                        SimpleNamespace(
                            id="found",
                            created_at=1,
                            status=SimpleNamespace(state="Paused"),
                        ),
                    ],
                ),
            ),
            close=AsyncMock(side_effect=RuntimeError("close failed")),
        )
        _FakeSandboxManager.create = AsyncMock(return_value=manager)

        with patch.dict(sys.modules, fake_opensandbox):
            ws = OpenSandboxWorkspace(workspace_id="wid")
            found = await ws._find_existing_sandbox()

        self.assertEqual(found.id, "found")
        manager.close.assert_awaited_once()

    async def test_wait_until_running_uses_sdk_is_healthy(self) -> None:
        sandbox = SimpleNamespace(is_healthy=AsyncMock(return_value=True))
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._sandbox = sandbox

        await ws._wait_until_running(timeout=0.1)

        sandbox.is_healthy.assert_awaited_once()

    async def test_wait_until_running_rejects_unhealthy_sdk_sandbox(
        self,
    ) -> None:
        sandbox = SimpleNamespace(is_healthy=AsyncMock(return_value=False))
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._sandbox = sandbox

        with self.assertRaises(RuntimeError):
            await ws._wait_until_running(timeout=0.01)

        self.assertGreaterEqual(sandbox.is_healthy.await_count, 1)

    async def test_provision_backend_uses_attach_or_create_helper(
        self,
    ) -> None:
        sandbox = _FakeSandbox("sbx_init")
        ws = OpenSandboxWorkspace(workspace_id="wid")

        async def _attach() -> None:
            ws._sandbox = sandbox

        with patch.object(
            OpenSandboxWorkspace,
            "_attach_or_create_sandbox",
            AsyncMock(side_effect=_attach),
        ) as attach_or_create, patch.object(
            OpenSandboxWorkspace,
            "_run_bootstrap",
            AsyncMock(),
        ):
            await ws._provision_backend()

        attach_or_create.assert_awaited_once()
        self.assertEqual(ws.sandbox_id, "sbx_init")
        self.assertIsNotNone(ws._backend)

    async def test_bootstrap_runs_when_marker_missing(self) -> None:
        sandbox = _FakeSandbox(marker_exists=False)
        with patch.object(
            OpenSandboxWorkspace,
            "_find_existing_sandbox",
            AsyncMock(return_value=None),
        ), patch.object(
            OpenSandboxWorkspace,
            "_create_sandbox",
            AsyncMock(return_value=sandbox),
        ), patch.object(
            OpenSandboxWorkspace,
            "_run_bootstrap",
            AsyncMock(),
        ) as bootstrap:
            ws = OpenSandboxWorkspace(workspace_id="wid")
            await ws._provision_backend()
        bootstrap.assert_awaited_once()

    async def test_bootstrap_skips_when_marker_exists(self) -> None:
        sandbox = _FakeSandbox(marker_exists=True)
        with patch.object(
            OpenSandboxWorkspace,
            "_find_existing_sandbox",
            AsyncMock(return_value=None),
        ), patch.object(
            OpenSandboxWorkspace,
            "_create_sandbox",
            AsyncMock(return_value=sandbox),
        ), patch.object(
            OpenSandboxWorkspace,
            "_run_bootstrap",
            AsyncMock(),
        ) as bootstrap:
            ws = OpenSandboxWorkspace(workspace_id="wid")
            await ws._provision_backend()
        bootstrap.assert_not_awaited()

    async def test_create_metadata_contains_workspace_id(self) -> None:
        ws = OpenSandboxWorkspace(
            workspace_id="wid",
            sandbox_metadata={METADATA_WORKSPACE_ID_KEY: "bad", "x": "y"},
        )
        metadata = ws._merged_metadata()
        self.assertEqual(metadata[METADATA_WORKSPACE_ID_KEY], "wid")
        self.assertEqual(metadata["x"], "y")

    async def test_missing_opensandbox_sdk_has_install_hint(self) -> None:
        ws = OpenSandboxWorkspace(workspace_id="wid")

        real_import_module = importlib.import_module

        def _import_module(
            name: str,
            package: str | None = None,
        ) -> types.ModuleType:
            if name.startswith("opensandbox"):
                raise ModuleNotFoundError(name=name)
            return real_import_module(name, package)

        with patch.object(
            importlib,
            "import_module",
            side_effect=_import_module,
        ):
            with self.assertRaises((ImportError, RuntimeError)) as ctx:
                ws._connection_config()

        self.assertIn("agentscope[workspace]", str(ctx.exception))
        self.assertNotIn("No module named 'opensandbox'", str(ctx.exception))

    async def test_connection_config_defaults_to_bootstrap_safe_timeout(
        self,
    ) -> None:
        fake_opensandbox = _fake_opensandbox_modules()

        with patch.dict(sys.modules, fake_opensandbox):
            ws = OpenSandboxWorkspace(workspace_id="wid")
            cfg = ws._connection_config()

        self.assertEqual(
            cfg.kwargs["request_timeout"],
            timedelta(seconds=600.0),
        )

    async def test_close_pauses_sandbox(self) -> None:
        sandbox = _FakeSandbox()
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._sandbox = sandbox
        ws._backend = object()
        ws.is_alive = True
        await ws.close()
        sandbox.pause.assert_awaited_once()
        sandbox.close.assert_awaited_once()
        self.assertFalse(ws.is_alive)

    async def test_list_tools_requires_initialized_backend(self) -> None:
        ws = OpenSandboxWorkspace(workspace_id="wid")
        with self.assertRaises(RuntimeError):
            await ws.list_tools()

    async def test_list_tools_returns_backend_bound_builtins(self) -> None:
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._backend = object()

        tools = await ws.list_tools()

        self.assertEqual(
            [tool.name for tool in tools],
            ["Bash", "Edit", "Glob", "Grep", "Read", "Write"],
        )

    async def test_add_mcp_rejects_duplicate(self) -> None:
        from agentscope.mcp import MCPClient, StdioMCPConfig

        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._gateway = AsyncMock()
        ws._gateway.add_mcp = AsyncMock(return_value=AsyncMock(name="browser"))
        ws._gateway_clients = {"browser": object()}
        mcp = MCPClient(
            name="browser",
            is_stateful=True,
            mcp_config=StdioMCPConfig(command="python", args=["server.py"]),
        )

        with self.assertRaises(ValueError):
            await ws.add_mcp(mcp)

    async def test_add_mcp_persists_gateway_client(self) -> None:
        from agentscope.mcp import MCPClient, StdioMCPConfig

        gw_client = AsyncMock()
        gw_client.name = "browser"
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._gateway = SimpleNamespace(make_client=lambda _spec: gw_client)
        ws._save_mcp_file = AsyncMock()
        mcp = MCPClient(
            name="browser",
            is_stateful=True,
            mcp_config=StdioMCPConfig(command="python", args=["server.py"]),
        )

        await ws.add_mcp(mcp)

        gw_client.connect.assert_awaited_once()
        self.assertEqual(ws._mcps, [mcp])
        self.assertIs(ws._gateway_clients["browser"], gw_client)
        ws._save_mcp_file.assert_awaited_once()

    async def test_remove_mcp_closes_and_persists(self) -> None:
        gw_client = AsyncMock()
        gw_client.name = "browser"
        sentinel = SimpleNamespace(name="browser")
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._gateway = object()
        ws._gateway_clients = {"browser": gw_client}
        ws._mcps = [sentinel]
        ws._save_mcp_file = AsyncMock()

        await ws.remove_mcp("browser")

        gw_client.close.assert_awaited_once()
        self.assertEqual(ws._mcps, [])
        self.assertEqual(ws._gateway_clients, {})
        ws._save_mcp_file.assert_awaited_once()

    async def test_list_skills_reads_frontmatter(self) -> None:
        from agentscope.skill import Skill
        from agentscope.workspace._opensandbox._bootstrap import (
            SANDBOX_SKILLS_DIR,
        )

        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._backend = _BackendDouble(
            {
                f"{SANDBOX_SKILLS_DIR}/demo/SKILL.md": (
                    b"---\nname: demo\ndescription: demo skill\n"
                    b"---\nbody text\n"
                ),
            },
        )

        skills = await ws.list_skills()

        self.assertEqual(
            skills,
            [
                Skill(
                    name="demo",
                    description="demo skill",
                    dir=f"{SANDBOX_SKILLS_DIR}/demo",
                    markdown="body text",
                    updated_at=0.0,
                ),
            ],
        )

    async def test_add_skill_rejects_missing_skill_md(self) -> None:
        ws = OpenSandboxWorkspace(workspace_id="wid")

        with self.assertRaises(ValueError):
            await ws.add_skill(str(Path(__file__).parent / "missing-skill"))

    async def test_remove_skill_deletes_matching_dir(self) -> None:
        from agentscope.skill import Skill

        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws.list_skills = AsyncMock(
            return_value=[
                Skill(
                    name="demo",
                    description="desc",
                    dir="/workspace/skills/demo",
                    markdown="",
                    updated_at=0.0,
                ),
            ],
        )
        ws._backend = AsyncMock()

        await ws.remove_skill("demo")

        ws._backend.delete_path.assert_awaited_once_with(
            "/workspace/skills/demo",
        )

    async def test_offload_tool_result_writes_text(self) -> None:
        from agentscope.message import ToolResultBlock

        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._backend = _BackendDouble()
        block = ToolResultBlock(id="tool-1", name="tool", output="hello")

        path = await ws.offload_tool_result("session-1", block)

        self.assertEqual(
            path,
            "/workspace/sessions/session-1/tool_result-tool-1.txt",
        )
        self.assertEqual(ws._backend.files[path], b"hello")

    async def test_offload_context_appends_jsonl(self) -> None:
        from agentscope.message import Msg, TextBlock

        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._backend = _BackendDouble()
        try:
            msg = Msg(name="user", role="user", content="hello")
        except Exception:
            msg = Msg(
                name="user",
                role="user",
                content=[TextBlock(text="hello")],
            )

        path = await ws.offload_context("session-1", [msg])

        self.assertEqual(path, "/workspace/sessions/session-1/context.jsonl")
        self.assertIn(b"hello", ws._backend.files[path])

    async def test_reset_clears_mcps_and_workspace_dirs(self) -> None:
        from agentscope.workspace._opensandbox._bootstrap import (
            SANDBOX_DATA_DIR,
            SANDBOX_SESSIONS_DIR,
            SANDBOX_SKILLS_DIR,
        )

        gw_client = AsyncMock()
        gw_client.name = "browser"
        ws = OpenSandboxWorkspace(workspace_id="wid")
        ws._backend = _BackendDouble()
        ws._gateway_clients = {"browser": gw_client}
        ws._mcps = [SimpleNamespace(name="browser")]

        await ws.reset()

        gw_client.close.assert_awaited_once()
        self.assertEqual(ws._gateway_clients, {})
        self.assertEqual(ws._mcps, [])
        self.assertEqual(
            ws._backend.deleted,
            [SANDBOX_SESSIONS_DIR, SANDBOX_DATA_DIR, SANDBOX_SKILLS_DIR],
        )
        self.assertEqual(ws._backend.files["/workspace/.mcp"], b"[]")


class TestOpenSandboxImportIsolation(unittest.TestCase):
    def test_e2b_workspace_test_imports_after_opensandbox_stub(self) -> None:
        sys.modules.pop("tests.workspace_e2b_test", None)

        with patch.dict(os.environ, {"E2B_API_KEY": "dummy"}):
            importlib.import_module("tests.workspace_e2b_test")


@unittest.skipUnless(
    _OPEN_SANDBOX_API_KEY and _OPEN_SANDBOX_DOMAIN,
    _OPEN_SANDBOX_SKIP,
)
class TestOpenSandboxWorkspaceLive(
    RemoteWorkspaceContractMixin,
    IsolatedAsyncioTestCase,
):
    async def asyncSetUp(self) -> None:
        self.workspace_cls = OpenSandboxWorkspace
        self.workspace_reopen_kwargs = {
            "api_key": _OPEN_SANDBOX_API_KEY,
            "domain": _OPEN_SANDBOX_DOMAIN,
        }
        self.workspace = OpenSandboxWorkspace(
            api_key=_OPEN_SANDBOX_API_KEY,
            domain=_OPEN_SANDBOX_DOMAIN,
        )
        await self.workspace.initialize()

    async def asyncTearDown(self) -> None:
        await self.workspace.close()


class TestOpenSandboxWorkspaceManager(
    RemoteWorkspaceManagerContractMixin,
    IsolatedAsyncioTestCase,
):
    async def asyncSetUp(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        self.manager = OpenSandboxWorkspaceManager()

    async def test_get_workspace_cache_miss_builds_and_caches(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        with patch(
            "agentscope.app.workspace_manager._opensandbox_workspace_manager."
            "OpenSandboxWorkspace",
        ) as cls:
            ws = AsyncMock()
            ws.workspace_id = "requested-id"
            cls.return_value = ws
            manager = OpenSandboxWorkspaceManager()
            got = await manager.get_workspace("u", "a", "s", "requested-id")

        self.assertIs(got, ws)
        self.assertIn("requested-id", manager._cache)
        self.assertIs(manager._cache["requested-id"][0], ws)
        ws.initialize.assert_awaited_once()

    async def test_get_workspace_builds_with_expected_args_and_metadata(
        self,
    ) -> None:
        from agentscope.mcp import MCPClient, StdioMCPConfig

        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        with patch(
            "agentscope.app.workspace_manager._opensandbox_workspace_manager."
            "OpenSandboxWorkspace",
        ) as cls:
            ws = AsyncMock()
            ws.workspace_id = "requested-id"
            cls.return_value = ws
            net = object()
            extra_mcp = MCPClient(
                name="browser",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="python", args=["mcp.py"]),
            )
            manager = OpenSandboxWorkspaceManager(
                image="python:3.12",
                api_key="api-key",
                domain="example.org",
                protocol="https",
                request_timeout_seconds=12.5,
                timeout_seconds=42,
                gateway_port=5601,
                env={"A": "1", "B": "2"},
                sandbox_metadata={"project": "unit"},
                resource={"cpu": "2", "mem": "1Gi"},
                entrypoint=["python", "-m", "main"],
                network_policy=net,
                extra_pip=["pip-a", "pip-b"],
                default_mcps=[extra_mcp],
                skill_paths=["/tmp/skills"],
            )
            await manager.get_workspace("u", "a", "s", "requested-id")

        cls.assert_called_once()
        kwargs = cls.call_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "requested-id")
        self.assertEqual(kwargs["image"], "python:3.12")
        self.assertEqual(kwargs["api_key"], "api-key")
        self.assertEqual(kwargs["domain"], "example.org")
        self.assertEqual(kwargs["protocol"], "https")
        self.assertEqual(kwargs["request_timeout_seconds"], 12.5)
        self.assertEqual(kwargs["timeout_seconds"], 42)
        self.assertEqual(kwargs["gateway_port"], 5601)
        self.assertEqual(kwargs["env"], {"A": "1", "B": "2"})
        self.assertEqual(kwargs["resource"], {"cpu": "2", "mem": "1Gi"})
        self.assertEqual(kwargs["entrypoint"], ["python", "-m", "main"])
        self.assertIs(kwargs["network_policy"], net)
        self.assertEqual(
            kwargs["sandbox_metadata"],
            {
                "agentscope.user.id": "u",
                "agentscope.agent.id": "a",
                "project": "unit",
            },
        )
        self.assertEqual(kwargs["extra_pip"], ["pip-a", "pip-b"])
        self.assertEqual(kwargs["default_mcps"], [extra_mcp])
        self.assertEqual(kwargs["skill_paths"], ["/tmp/skills"])

    async def test_close_pops_cached_workspace_and_closes(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        manager = OpenSandboxWorkspaceManager()
        ws = AsyncMock()
        ws.workspace_id = "w1"
        manager._cache = {"w1": (ws, 0.0)}
        await manager.close("w1")
        ws.close.assert_awaited_once()
        self.assertEqual(manager._cache, {})

    async def test_close_missing_id_is_noop(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        manager = OpenSandboxWorkspaceManager()
        ws = AsyncMock()
        ws.close = AsyncMock()
        manager._cache = {"w1": (ws, 0.0)}
        await manager.close("missing")
        ws.close.assert_not_awaited()
        self.assertEqual(list(manager._cache.keys()), ["w1"])

    async def test_cache_hit_reuses_workspace(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        manager = OpenSandboxWorkspaceManager()
        ws = AsyncMock()
        ws.workspace_id = "wid"
        manager._cache["wid"] = (ws, 0.0)
        got = await manager.get_workspace("u", "a", "s", "wid")
        self.assertIs(got, ws)

    async def test_sweep_once_evicts_expired_only(self) -> None:
        import time

        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        now = time.monotonic()
        expired = AsyncMock()
        expired.workspace_id = "w1"
        keep = AsyncMock()
        keep.workspace_id = "w2"
        manager = OpenSandboxWorkspaceManager(ttl=10.0)
        manager._cache = {"w1": (expired, now - 20.0), "w2": (keep, now - 1.0)}
        await manager._sweep_once()

        expired.close.assert_awaited_once()
        keep.close.assert_not_awaited()
        self.assertIn("w2", manager._cache)
        self.assertIs(manager._cache["w2"][0], keep)
        self.assertNotIn("w1", manager._cache)

    async def test_aenter_aexit_starts_and_stops_sweep_task(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        manager = OpenSandboxWorkspaceManager(sweep_interval=99999)
        async with manager:
            self.assertIsNotNone(manager._sweep_task)
            close_all = AsyncMock()
            manager.close_all = close_all

        self.assertIsNone(manager._sweep_task)
        close_all.assert_awaited_once()

    async def test_create_workspace_initializes_and_caches(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        with patch(
            "agentscope.app.workspace_manager._opensandbox_workspace_manager."
            "OpenSandboxWorkspace",
        ) as cls:
            ws = AsyncMock()
            ws.workspace_id = "wid"
            cls.return_value = ws
            manager = OpenSandboxWorkspaceManager()
            created = await manager.create_workspace("u", "a", "s")

        self.assertIs(created, ws)
        ws.initialize.assert_awaited_once()

    async def test_close_all_closes_cached_workspaces(self) -> None:
        OpenSandboxWorkspaceManager = _opensandbox_manager_cls()
        manager = OpenSandboxWorkspaceManager()
        ws1 = AsyncMock()
        ws1.workspace_id = "w1"
        ws2 = AsyncMock()
        ws2.workspace_id = "w2"
        manager._cache = {"w1": (ws1, 0.0), "w2": (ws2, 0.0)}
        await manager.close_all()
        ws1.close.assert_awaited_once()
        ws2.close.assert_awaited_once()
        self.assertEqual(manager._cache, {})


if __name__ == "__main__":
    unittest.main()
