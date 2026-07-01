# -*- coding: utf-8 -*-
# pylint: disable=missing-class-docstring,missing-function-docstring
"""Suite-safe public export checks for OpenSandbox workspace modules."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import TestCase


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"

_CHILD_IMPORT_PREAMBLE = textwrap.dedent(
    """
    import sys
    import types
    from dataclasses import dataclass
    from pathlib import Path

    src_root = Path(r"{src_root}") / "agentscope"

    def install_stubs():
        if "httpx" not in sys.modules:
            httpx = types.ModuleType("httpx")

            class _AsyncClient:
                def __init__(self, *args, **kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return None

                async def aclose(self):
                    return None

            class _Response:
                status_code = 200
                text = ""

                def json(self):
                    return {{}}

                def raise_for_status(self):
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
                def __init__(self):
                    self.short_description = None
                    self.long_description = None
                    self.params = []

            def _parse(_docstring):
                return _ParsedDocstring()

            docstring_parser.parse = _parse
            sys.modules["docstring_parser"] = docstring_parser

        if "mcp" not in sys.modules:
            mcp = types.ModuleType("mcp")
            mcp_types = types.ModuleType("mcp.types")

            class _Tool:
                def __init__(
                    self,
                    name="",
                    description=None,
                    inputSchema=None,
                    annotations=None,
                ):
                    self.name = name
                    self.description = description
                    self.inputSchema = inputSchema
                    self.annotations = annotations

                @classmethod
                def model_validate(cls, data):
                    if isinstance(data, cls):
                        return data
                    return cls(**data)

            mcp_types.Tool = _Tool
            mcp.types = mcp_types
            sys.modules["mcp"] = mcp
            sys.modules["mcp.types"] = mcp_types

        if "pydantic" not in sys.modules:
            pydantic = types.ModuleType("pydantic")

            class _AnyUrl(str):
                pass

            def _private_attr(default=None, *, default_factory=None):
                if default is not None:
                    return default
                if default_factory is not None:
                    return default_factory()
                return None

            class _BaseModel:
                def __init__(self, *args, **kwargs):
                    pass

            pydantic.AnyUrl = _AnyUrl
            pydantic.PrivateAttr = _private_attr
            pydantic.BaseModel = _BaseModel
            sys.modules["pydantic"] = pydantic

        if "agentscope.mcp" not in sys.modules:
            agentscope_mcp = types.ModuleType("agentscope.mcp")

            @dataclass
            class _StdioMCPConfig:
                command: str
                args: list[str]

                def model_dump(self, mode="json"):
                    return {{
                        "type": "stdio",
                        "command": self.command,
                        "args": list(self.args),
                    }}

            @dataclass
            class _MCPClient:
                name: str
                mcp_config: _StdioMCPConfig

                def model_dump(self, mode="json"):
                    return {{
                        "name": self.name,
                        "mcp_config": self.mcp_config.model_dump(mode=mode),
                    }}

                @classmethod
                def model_validate(cls, data):
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
                    return cls(name=data["name"], mcp_config=mcp_config)

            agentscope_mcp.MCPClient = _MCPClient
            agentscope_mcp.StdioMCPConfig = _StdioMCPConfig
            sys.modules["agentscope.mcp"] = agentscope_mcp

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
                def __init__(self, name, role, content):
                    self.name = name
                    self.role = role
                    self.content = content

                def model_dump_json(self):
                    return ""

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
            agentscope_message.ToolResultState = types.SimpleNamespace(
                ERROR="error",
            )
            sys.modules["agentscope.message"] = agentscope_message

        if "agentscope.permission" not in sys.modules:
            agentscope_permission = types.ModuleType("agentscope.permission")

            class _PermissionBehavior:
                ALLOW = "allow"
                ASK = "ask"

            class _PermissionDecision:
                def __init__(self, behavior=None, message=""):
                    self.behavior = behavior
                    self.message = message

            agentscope_permission.PermissionBehavior = _PermissionBehavior
            agentscope_permission.PermissionDecision = _PermissionDecision
            sys.modules["agentscope.permission"] = agentscope_permission

        if "agentscope.tool" not in sys.modules:
            agentscope_tool = types.ModuleType("agentscope.tool")

            class _BackendBase:
                pass

            class _BuiltinTool:
                name = ""

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

            class _ExecResult:
                def __init__(self, exit_code, stdout, stderr):
                    self.exit_code = exit_code
                    self.stdout = stdout
                    self.stderr = stderr

                def ok(self):
                    return self.exit_code == 0

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

        if "agentscope.tool._builtin._backend" not in sys.modules:
            builtin_backend = types.ModuleType(
                "agentscope.tool._builtin._backend",
            )

            class _LocalBackend:
                def __init__(self, *args, **kwargs):
                    self.args = args
                    self.kwargs = kwargs

                async def exec_shell(self, *args, **kwargs):
                    return None

                async def read_file(self, *args, **kwargs):
                    return b""

                async def write_file(self, *args, **kwargs):
                    return None

            builtin_backend.LocalBackend = _LocalBackend
            sys.modules["agentscope.tool._builtin._backend"] = builtin_backend

        if "aiofiles" not in sys.modules:
            aiofiles = types.ModuleType("aiofiles")
            aiofiles.os = types.ModuleType("aiofiles.os")
            sys.modules["aiofiles"] = aiofiles
            sys.modules["aiofiles.os"] = aiofiles.os

        if "agentscope.tool._builtin" not in sys.modules:
            builtin_tools = types.ModuleType("agentscope.tool._builtin")

            class _BuiltinTool:
                def __init__(self, *args, **kwargs):
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

            builtin_tools.Bash = Bash
            builtin_tools.Edit = Edit
            builtin_tools.Glob = Glob
            builtin_tools.Grep = Grep
            builtin_tools.Read = Read
            builtin_tools.Write = Write
            sys.modules["agentscope.tool._builtin"] = builtin_tools

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

        if "frontmatter" not in sys.modules:
            frontmatter = types.ModuleType("frontmatter")

            class _Doc(dict):
                def __init__(self, data, content):
                    super().__init__(data)
                    self.content = content

            def _loads(text):
                if not text.startswith("---"):
                    return _Doc({{}}, text)
                _sep, rest = text.split("---", 1)
                header, content = rest.split("---", 1)
                data = {{}}
                for line in header.strip().splitlines():
                    key, value = line.split(":", 1)
                    data[key.strip()] = value.strip().strip('"')
                return _Doc(data, content.lstrip())

            frontmatter.loads = _loads
            sys.modules["frontmatter"] = frontmatter

        app_pkg = types.ModuleType("agentscope.app")
        app_pkg.__path__ = [str(src_root / "app")]
        sys.modules["agentscope.app"] = app_pkg

    install_stubs()
    """,
).format(src_root=str(_SRC_ROOT))


class TestOpenSandboxPublicExports(TestCase):
    maxDiff = None

    def _run_child_import_check(self, assertion_code: str) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(_SRC_ROOT)
        code = _CHILD_IMPORT_PREAMBLE + "\n" + textwrap.dedent(assertion_code)
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env=env,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                "child import check failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}",
            )

    def test_workspace_import_and_exports(self) -> None:
        self._run_child_import_check(
            """
            from agentscope.workspace import (
                OpenSandboxBackend,
                OpenSandboxWorkspace,
                __all__ as workspace_all,
            )

            assert OpenSandboxBackend is not None
            assert OpenSandboxWorkspace is not None
            assert "OpenSandboxBackend" in workspace_all
            assert "OpenSandboxWorkspace" in workspace_all
            """,
        )

    def test_manager_import_and_exports(self) -> None:
        self._run_child_import_check(
            """
            import importlib

            module = importlib.import_module(
                "agentscope.app.workspace_manager",
            )
            manager_all = getattr(module, "__all__", [])

            assert "OpenSandboxWorkspaceManager" in manager_all

            from agentscope.app.workspace_manager import (
                OpenSandboxWorkspaceManager,
            )

            assert OpenSandboxWorkspaceManager is not None
            """,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
