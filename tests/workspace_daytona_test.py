# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaWorkspace`.

Most tests patch the Daytona SDK boundary so they run in normal CI.
Live tests are opt-in via ``DAYTONA_API_KEY``.
"""

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from enum import Enum
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from _daytona_live_utils import (
    DAYTONA_API_KEY,
    SKIP_REASON,
    delete_live_daytona_workspace,
    live_daytona_kwargs,
    live_daytona_workspace_id,
)

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import BackgroundTaskManager, SchedulerManager
from agentscope.app._service import get_toolkit
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
)
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.permission import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import ExecResult, ToolResponse
from agentscope.workspace import DaytonaBackend, DaytonaWorkspace
from agentscope.workspace._daytona import _daytona_workspace as daytona_mod
from agentscope.workspace._daytona._bootstrap import (
    DATA_DIR_NAME,
    DEFAULT_GATEWAY_PORT,
    DEV_SRC_DIR_NAME,
    DEV_SRC_TAR_NAME,
    GATEWAY_HOME_NAME,
    GATEWAY_CONFIG_NAME,
    GATEWAY_LOG_NAME,
    GATEWAY_SCRIPT_NAME,
    GLOB_HELPER_NAME,
    METADATA_WORKSPACE_ID_KEY,
    MCP_FILE_NAME,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    bootstrap_commands,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)
from agentscope.workspace._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _read_glob_helper_bytes,
)


class _NullBus:
    """Message bus placeholder for service toolkit assembly."""


class _NoOpStorage:
    """Storage placeholder for tools that only bind the reference."""


class _FakeProcess:
    """Daytona process fake."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(self, command: str, **_kwargs: object) -> object:
        """Record command and return success."""
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, result="", stdout="", stderr="")


class _FakeFS:
    """Daytona fs fake."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def download_file(self, path: str) -> bytes:
        """Return stored file bytes."""
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def upload_file(self, file: bytes, remote_path: str) -> None:
        """Store uploaded bytes."""
        self.files[remote_path] = file


class _FakeSandbox:
    """Daytona sandbox fake for workspace tests."""

    def __init__(
        self,
        sandbox_id: str = "sandbox-1",
        *,
        state: str = "started",
        recoverable: bool | None = None,
        workdir: str = "/home/daytona",
        user_home: str = "/home/daytona",
    ) -> None:
        self.id = sandbox_id
        self.name = sandbox_id
        self.state = state
        self.recoverable = recoverable
        self.workdir = workdir
        self.user_home = user_home
        self.labels: dict[str, str] = {}
        self.process = _FakeProcess()
        self.fs = _FakeFS()
        self.started = False
        self.recovered = False
        self.stopped: list[dict[str, object]] = []
        self.waited_for_stop = False
        self.preview = SimpleNamespace(url="https://preview.example", token="ptok")

    async def get_work_dir(self) -> str:
        """Return SDK-derived workdir."""
        return self.workdir

    async def get_user_home_dir(self) -> str:
        """Return SDK-derived user home."""
        return self.user_home

    async def start(self, timeout: float | None = 60) -> None:
        """Mark sandbox started."""
        self.started = True
        self.state = "started"

    async def recover(self, timeout: float | None = 60) -> None:
        """Mark sandbox recovered."""
        self.recovered = True
        self.state = "started"

    async def stop(
        self,
        timeout: float | None = 60,
        force: bool = False,
    ) -> None:
        """Record graceful stop."""
        self.stopped.append({"timeout": timeout, "force": force})
        self.state = "stopped"

    async def wait_for_sandbox_start(
        self,
        timeout: float | None = 60,
    ) -> None:
        """No-op wait hook."""

    async def wait_for_sandbox_stop(
        self,
        timeout: float | None = 60,
    ) -> None:
        """Mark stop wait."""
        self.waited_for_stop = True
        self.state = "stopped"

    async def refresh_data(self) -> None:
        """No-op refresh hook."""

    async def get_preview_link(self, port: int) -> object:
        """Return preview URL/token."""
        if hasattr(self.preview, "__dict__"):
            self.preview.port = port
        return self.preview


class _MappedProcess:
    """Execute Daytona process commands against a mapped local directory."""

    def __init__(self, host_root: str, sandbox_root: str) -> None:
        self.host_root = host_root
        self.sandbox_root = sandbox_root

    def _to_host(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace(self.sandbox_root, self.host_root)

    def _to_sandbox(self, value: str) -> str:
        return value.replace(self.host_root, self.sandbox_root)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> object:
        """Run the shell command locally with path translation."""
        mapped_command = self._to_host(command)
        mapped_cwd = self._to_host(cwd) or self.host_root
        env = dict(os.environ)
        env["PATH"] = (
            os.path.dirname(sys.executable)
            + os.pathsep
            + env.get("PATH", "")
        )
        proc = await asyncio.create_subprocess_shell(
            mapped_command,
            cwd=mapped_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return SimpleNamespace(
                exit_code=-1,
                result="",
                stdout="",
                stderr="timed out",
            )
        stdout_text = self._to_sandbox(stdout.decode("utf-8", "replace"))
        stderr_text = self._to_sandbox(stderr.decode("utf-8", "replace"))
        return SimpleNamespace(
            exit_code=proc.returncode,
            result=stdout_text,
            stdout=stdout_text,
            stderr=stderr_text,
        )


class _MappedFS:
    """Daytona fs fake backed by local files."""

    def __init__(self, host_root: str, sandbox_root: str) -> None:
        self.host_root = host_root
        self.sandbox_root = sandbox_root

    def _to_host(self, path: str) -> str:
        return path.replace(self.sandbox_root, self.host_root)

    async def download_file(self, path: str) -> bytes:
        """Read mapped file bytes."""
        host_path = self._to_host(path)
        if not os.path.exists(host_path):
            raise FileNotFoundError(path)
        with open(host_path, "rb") as f:
            return f.read()

    async def upload_file(self, file: bytes, remote_path: str) -> None:
        """Write mapped file bytes."""
        host_path = self._to_host(remote_path)
        os.makedirs(os.path.dirname(host_path), exist_ok=True)
        with open(host_path, "wb") as f:
            f.write(file)


class _MappedSandbox(_FakeSandbox):
    """Fake Daytona sandbox whose process/fs operate on a temp dir."""

    def __init__(self, host_root: str) -> None:
        super().__init__("mapped-sandbox")
        self.host_root = host_root
        self.sandbox_root = "/home/daytona"
        self.process = _MappedProcess(host_root, self.sandbox_root)
        self.fs = _MappedFS(host_root, self.sandbox_root)

    async def get_work_dir(self) -> str:
        """Return mapped sandbox workdir."""
        return self.sandbox_root

    async def get_user_home_dir(self) -> str:
        """Return mapped sandbox home."""
        return self.sandbox_root


class _FakeDaytona:
    """AsyncDaytona fake."""

    instances: list["_FakeDaytona"] = []
    list_result: list[_FakeSandbox] = []
    created_params: list[object] = []
    configs: list[object] = []

    def __init__(self, config: object | None = None) -> None:
        self.config = config
        self.created: list[_FakeSandbox] = []
        self.closed = False
        _FakeDaytona.instances.append(self)
        _FakeDaytona.configs.append(config)

    async def create(self, params: object | None = None, **_kwargs: object) -> _FakeSandbox:
        """Create and return a fake sandbox."""
        _FakeDaytona.created_params.append(params)
        sandbox = _FakeSandbox(f"sandbox-created-{len(self.created) + 1}")
        self.created.append(sandbox)
        return sandbox

    async def get(self, sandbox_id: str) -> _FakeSandbox:
        """Return a fake sandbox by id."""
        for sandbox in _FakeDaytona.list_result:
            if sandbox.id == sandbox_id:
                return sandbox
        return _FakeSandbox(sandbox_id)

    async def list(self, query: object | None = None) -> list[_FakeSandbox]:
        """Return fake candidates, filtered by labels when possible."""
        labels = getattr(query, "labels", None)
        if labels:
            return [
                sandbox
                for sandbox in _FakeDaytona.list_result
                if all(sandbox.labels.get(k) == v for k, v in labels.items())
            ]
        return list(_FakeDaytona.list_result)

    async def close(self) -> None:
        """Mark SDK client closed."""
        self.closed = True


class _FakeCreateSandboxFromSnapshotParams:
    """Capture Daytona create params without importing the real SDK."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeDaytonaConfig:
    """Capture Daytona config values."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeListSandboxesQuery:
    """Capture list filters."""

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _FakeSandboxState(str, Enum):
    """Subset of Daytona sandbox states used by the workspace."""

    STARTED = "started"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"
    PAUSED = "paused"
    RESUMING = "resuming"


class _FakeGateway:
    """GatewayClient fake."""

    instances: list["_FakeGateway"] = []

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})
        self.closed = False
        _FakeGateway.instances.append(self)

    async def health(self) -> bool:
        """Always healthy."""
        return True

    async def list_mcps(self) -> list[MCPClient]:
        """No registered MCPs by default."""
        return []

    async def aclose(self) -> None:
        """Mark closed."""
        self.closed = True

    def make_client(self, spec: dict[str, object]) -> MCPClient:
        """Build a regular MCP client from spec for add_mcp tests."""
        return _FakeGatewayMCPClient(str(spec["name"]))


class _FakeGatewayMCPClient:
    """Small gateway MCP client fake for add/remove tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        """Mark connected."""
        self.connected = True

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


def _install_fake_daytona_module() -> types.ModuleType:
    """Install a fake ``daytona`` module into ``sys.modules``."""
    mod = types.ModuleType("daytona")
    mod.AsyncDaytona = _FakeDaytona
    mod.DaytonaConfig = _FakeDaytonaConfig
    mod.CreateSandboxFromSnapshotParams = _FakeCreateSandboxFromSnapshotParams
    mod.ListSandboxesQuery = _FakeListSandboxesQuery
    mod.SandboxState = _FakeSandboxState
    mod.DaytonaError = RuntimeError
    sys.modules["daytona"] = mod
    return mod


class TestDaytonaBootstrapHelpers(IsolatedAsyncioTestCase):
    """Pure bootstrap command rendering tests."""

    async def test_bootstrap_commands_include_layout_tools_and_extra_pip(
        self,
    ) -> None:
        """Bootstrap commands provision layout, tools, venv and packages."""
        commands = bootstrap_commands(
            workdir="/work",
            data_dir="/work/data",
            skills_dir="/work/skills",
            sessions_dir="/work/sessions",
            user_home="/home/daytona",
            gateway_home="/home/daytona/.agentscope",
            gateway_venv="/home/daytona/.agentscope/.venv",
            gateway_venv_py="/home/daytona/.agentscope/.venv/bin/python",
            uv_bin="/home/daytona/.local/bin/uv",
            extra_pip=["extra-a", "extra-b"],
            install_agentscope_cmd="install-agentscope",
        )

        self.assertEqual(len(commands), 6)
        self.assertEqual(
            commands[0],
            "mkdir -p /work /work/data /work/skills "
            "/work/sessions /home/daytona/.agentscope",
        )
        self.assertIn("apt-get install", commands[1])
        self.assertIn("ripgrep", commands[1])
        self.assertIn("UV_INSTALL_DIR=/home/daytona/.local/bin", commands[2])
        self.assertEqual(
            commands[3],
            "/home/daytona/.local/bin/uv venv "
            "/home/daytona/.agentscope/.venv",
        )
        for package in (*_GATEWAY_BASE_REQUIREMENTS, "extra-a", "extra-b"):
            self.assertIn(package, commands[4])
        self.assertEqual(commands[5], "install-agentscope")

    async def test_render_install_agentscope_commands(self) -> None:
        """Released and dev install commands are rendered deterministically."""
        released = render_install_agentscope_cmd_released(
            uv_bin="/u/uv",
            gateway_venv_py="/venv/bin/python",
            version="1.2.3",
        )
        self.assertEqual(
            released,
            "/u/uv pip install --python /venv/bin/python "
            "--no-deps 'agentscope==1.2.3'",
        )

        dev = render_install_agentscope_cmd_dev(
            uv_bin="/u/uv",
            gateway_venv_py="/venv/bin/python",
            dev_src_tar="/tmp/src.tar",
            dev_src_dir="/tmp/src",
        )
        self.assertEqual(
            dev,
            "mkdir -p /tmp/src && tar -xf /tmp/src.tar -C /tmp/src && "
            "/u/uv pip install --python /venv/bin/python --no-deps /tmp/src "
            "&& rm -rf /tmp/src.tar /tmp/src",
        )


class TestDaytonaWorkspaceMock(IsolatedAsyncioTestCase):
    """Workspace lifecycle and configuration tests with a fake SDK."""

    async def asyncSetUp(self) -> None:
        """Patch Daytona and gateway boundaries."""
        _FakeDaytona.instances.clear()
        _FakeDaytona.list_result.clear()
        _FakeDaytona.created_params.clear()
        _FakeDaytona.configs.clear()
        _FakeGateway.instances.clear()
        self.fake_daytona_mod = _install_fake_daytona_module()
        self.gateway_patch = patch.object(
            daytona_mod,
            "GatewayClient",
            _FakeGateway,
        )
        self.gateway_patch.start()
        self.bootstrap_patch = patch.object(
            DaytonaWorkspace,
            "_run_bootstrap",
            AsyncMock(),
        )
        self.bootstrap_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.bootstrap_patch.stop()
        self.gateway_patch.stop()
        sys.modules.pop("daytona", None)

    async def test_create_uses_minimal_params_and_sdk_paths(self) -> None:
        """Create passes labels/env and the secure public default."""
        workspace = DaytonaWorkspace(
            workspace_id="wid-1",
            api_key="key",
            api_url="https://daytona.example/api",
            target="us",
            env={"A": "B"},
            sandbox_metadata={"team": "agents"},
        )

        await workspace.initialize()

        self.assertEqual(workspace.workdir, "/home/daytona")
        self.assertEqual(workspace._user_home, "/home/daytona")
        self.assertIsInstance(workspace._backend, DaytonaBackend)
        self.assertEqual(
            _FakeDaytona.configs[0].kwargs,
            {
                "api_key": "key",
                "api_url": "https://daytona.example/api",
                "target": "us",
            },
        )
        params = _FakeDaytona.created_params[0]
        self.assertIsInstance(params, _FakeCreateSandboxFromSnapshotParams)
        self.assertEqual(
            params.kwargs,
            {
                "env_vars": {"A": "B"},
                "labels": {
                    METADATA_WORKSPACE_ID_KEY: "wid-1",
                    "team": "agents",
                },
                "public": False,
            },
        )

    async def test_initialize_is_idempotent(self) -> None:
        """Repeated ``initialize`` calls do not create new runtime state."""
        workspace = DaytonaWorkspace(workspace_id="wid-idempotent")

        await workspace.initialize()
        await workspace.initialize()

        self.assertTrue(workspace.is_alive)
        self.assertEqual(len(_FakeDaytona.created_params), 1)
        self.assertEqual(len(_FakeGateway.instances), 1)

    async def test_empty_config_uses_sdk_environment_defaults(self) -> None:
        """No explicit connection config means ``AsyncDaytona()``."""
        workspace = DaytonaWorkspace(workspace_id="wid-2")

        await workspace._get_daytona_client()

        self.assertIsNone(_FakeDaytona.configs[0])

    async def test_initialize_reuses_stopped_candidate_and_starts_it(self) -> None:
        """Stopped candidates are started instead of creating a new one."""
        candidate = _FakeSandbox("sandbox-old", state="stopped")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-3"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-3")
        await workspace.initialize()

        self.assertIs(workspace._sandbox, candidate)
        self.assertTrue(candidate.started)
        self.assertEqual(_FakeDaytona.created_params, [])

    async def test_initialize_waits_for_stopping_candidate_then_starts_it(self) -> None:
        """Stopping candidates are waited to stopped before start."""
        candidate = _FakeSandbox("sandbox-stopping", state="stopping")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-stopping"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-stopping")
        await workspace.initialize()

        self.assertTrue(candidate.waited_for_stop)
        self.assertTrue(candidate.started)

    async def test_initialize_derives_all_paths_from_sdk_values(self) -> None:
        """Workspace paths are rooted in SDK workdir and home values."""
        candidate = _FakeSandbox(
            "sandbox-custom-paths",
            workdir="/workspace/project",
            user_home="/users/daytona",
        )
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-custom-paths"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-custom-paths")
        await workspace.initialize()

        self.assertEqual(workspace.workdir, "/workspace/project")
        self.assertEqual(workspace._user_home, "/users/daytona")
        self.assertEqual(workspace._data_dir, "/workspace/project/data")
        self.assertEqual(workspace._skills_dir, "/workspace/project/skills")
        self.assertEqual(workspace._sessions_dir, "/workspace/project/sessions")
        self.assertEqual(workspace._mcp_file, "/workspace/project/.mcp")
        self.assertEqual(workspace._gateway_home, "/users/daytona/.agentscope")
        self.assertEqual(
            workspace._gateway_venv,
            "/users/daytona/.agentscope/.venv",
        )
        self.assertEqual(
            workspace._gateway_script,
            "/users/daytona/.agentscope/_mcp_gateway_app.py",
        )
        self.assertEqual(
            workspace._glob_helper_script,
            "/users/daytona/.agentscope/_glob_helper.py",
        )
        self.assertEqual(
            workspace._gateway_config,
            "/users/daytona/.agentscope/gateway.config.json",
        )
        self.assertEqual(
            workspace._gateway_log,
            "/users/daytona/.agentscope/gateway.log",
        )
        self.assertEqual(workspace._uv_bin, "/users/daytona/.local/bin/uv")
        self.assertEqual(
            workspace._dev_src_tar,
            "/users/daytona/.agentscope/agentscope_src.tar",
        )
        self.assertEqual(
            workspace._dev_src_dir,
            "/users/daytona/.agentscope/agentscope_src",
        )

    async def test_initialize_recovers_recoverable_error_candidate(self) -> None:
        """Recoverable candidates are recovered before use."""
        candidate = _FakeSandbox(
            "sandbox-error",
            state="error",
            recoverable=True,
        )
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-4"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-4")
        await workspace.initialize()

        self.assertIs(workspace._sandbox, candidate)
        self.assertTrue(candidate.recovered)

    async def test_initialize_skips_unrecoverable_error_candidate(self) -> None:
        """Unrecoverable candidates are ignored and a new sandbox is made."""
        candidate = _FakeSandbox(
            "sandbox-error",
            state="error",
            recoverable=False,
        )
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-5"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-5")
        await workspace.initialize()

        self.assertNotEqual(workspace._sandbox.id, "sandbox-error")
        self.assertEqual(len(_FakeDaytona.created_params), 1)

    async def test_gateway_uses_preview_url_and_token_header(self) -> None:
        """GatewayClient receives Daytona preview URL and preview token."""
        workspace = DaytonaWorkspace(
            workspace_id="wid-6",
            gateway_port=DEFAULT_GATEWAY_PORT,
        )

        await workspace.initialize()

        gateway = _FakeGateway.instances[0]
        self.assertEqual(gateway.base_url, "https://preview.example")
        self.assertEqual(
            gateway.extra_headers,
            {"x-daytona-preview-token": "ptok"},
        )

    async def test_gateway_preview_without_token_has_no_extra_headers(self) -> None:
        """Preview auth header is omitted when Daytona returns no token."""
        candidate = _FakeSandbox("sandbox-no-preview-token")
        candidate.preview = SimpleNamespace(url="https://preview.example", token=None)
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-no-preview-token"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-no-preview-token")
        await workspace.initialize()

        gateway = _FakeGateway.instances[0]
        self.assertEqual(gateway.base_url, "https://preview.example")
        self.assertEqual(gateway.extra_headers, {})

    async def test_gateway_accepts_preview_url_attribute(self) -> None:
        """Preview URL extraction accepts SDK ``preview_url`` variants."""
        candidate = _FakeSandbox("sandbox-preview-url")
        candidate.preview = SimpleNamespace(
            preview_url="https://preview-url.example/path/",
            token="tok",
        )
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-preview-url"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-preview-url")
        await workspace.initialize()

        self.assertEqual(
            _FakeGateway.instances[0].base_url,
            "https://preview-url.example/path",
        )

    async def test_gateway_accepts_string_preview_response(self) -> None:
        """Preview URL extraction accepts a raw string response."""
        candidate = _FakeSandbox("sandbox-preview-string")
        candidate.preview = "https://preview-string.example/"
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-preview-string"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-preview-string")
        await workspace.initialize()

        gateway = _FakeGateway.instances[0]
        self.assertEqual(gateway.base_url, "https://preview-string.example")
        self.assertEqual(gateway.extra_headers, {})

    async def test_list_tools_requires_initialized_backend(self) -> None:
        """Builtin tools cannot silently fall back to local backend."""
        workspace = DaytonaWorkspace(workspace_id="wid-7")

        with self.assertRaises(RuntimeError):
            await workspace.list_tools()

    async def test_list_tools_returns_six_builtin_tools(self) -> None:
        """Initialized workspace exposes the six builtin tools."""
        workspace = DaytonaWorkspace(workspace_id="wid-8")
        await workspace.initialize()

        tools = await workspace.list_tools()

        self.assertEqual(
            sorted(tool.name for tool in tools),
            ["Bash", "Edit", "Glob", "Grep", "Read", "Write"],
        )

    async def test_mcp_file_roundtrip_uses_sdk_workdir(self) -> None:
        """The persisted ``.mcp`` file is rooted at the SDK workdir."""
        workspace = DaytonaWorkspace(workspace_id="wid-9")
        await workspace.initialize()
        workspace._mcps = [
            MCPClient(
                name="demo",
                mcp_config=StdioMCPConfig(command="node", args=["server.js"]),
                is_stateful=True,
            ),
        ]

        await workspace._save_mcp_file()

        raw = await workspace._backend.read_file("/home/daytona/.mcp")
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data[0]["name"], "demo")

    async def test_missing_mcp_file_seeds_default_mcps(self) -> None:
        """Missing persisted MCP config falls back to configured defaults."""
        default_mcp = MCPClient(
            name="default",
            mcp_config=StdioMCPConfig(command="python", args=["server.py"]),
            is_stateful=True,
        )
        workspace = DaytonaWorkspace(
            workspace_id="wid-default-mcp",
            default_mcps=[default_mcp],
        )

        await workspace.initialize()

        self.assertEqual([m.name for m in workspace._mcps], ["default"])
        raw = await workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8"))[0]["name"], "default")

    async def test_invalid_mcp_file_falls_back_to_default_mcps(self) -> None:
        """Invalid persisted MCP JSON falls back to configured defaults."""
        candidate = _FakeSandbox("sandbox-invalid-mcp")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-invalid-mcp"}
        candidate.fs.files["/home/daytona/.mcp"] = b"{not json"
        _FakeDaytona.list_result[:] = [candidate]
        default_mcp = MCPClient(
            name="fallback",
            mcp_config=StdioMCPConfig(command="python", args=["server.py"]),
            is_stateful=True,
        )
        workspace = DaytonaWorkspace(
            workspace_id="wid-invalid-mcp",
            default_mcps=[default_mcp],
        )

        await workspace.initialize()

        self.assertEqual([m.name for m in workspace._mcps], ["fallback"])
        raw = await workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8"))[0]["name"], "fallback")

    async def test_run_bootstrap_error_includes_command_and_output(self) -> None:
        """Bootstrap failures include command, exit code, stderr and stdout."""
        self.bootstrap_patch.stop()
        try:
            workspace = DaytonaWorkspace(workspace_id="wid-bootstrap-error")
            workspace.workdir = "/work"
            workspace._data_dir = "/work/data"
            workspace._skills_dir = "/work/skills"
            workspace._sessions_dir = "/work/sessions"
            workspace._user_home = "/home/daytona"
            workspace._gateway_home = "/home/daytona/.agentscope"
            workspace._gateway_venv = "/home/daytona/.agentscope/.venv"
            workspace._gateway_venv_py = (
                "/home/daytona/.agentscope/.venv/bin/python"
            )
            workspace._uv_bin = "/home/daytona/.local/bin/uv"
            workspace._dev_src_tar = "/home/daytona/.agentscope/agentscope_src.tar"
            workspace._dev_src_dir = "/home/daytona/.agentscope/agentscope_src"
            workspace._glob_helper_script = "/home/daytona/.agentscope/glob.py"
            workspace._gateway_script = "/home/daytona/.agentscope/gateway.py"
            backend = SimpleNamespace(
                exec_shell=AsyncMock(
                    return_value=ExecResult(
                        exit_code=9,
                        stdout=b"bootstrap stdout",
                        stderr=b"bootstrap stderr",
                    ),
                ),
                write_file=AsyncMock(),
            )
            workspace._backend = backend

            with (
                patch.object(daytona_mod, "_is_released_install", return_value=True),
                patch.object(daytona_mod, "_agentscope_version", return_value="1.2.3"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "DaytonaWorkspace bootstrap failed",
                ) as cm:
                    await workspace._run_bootstrap()

            msg = str(cm.exception)
            self.assertIn("exit 9", msg)
            self.assertIn("mkdir -p /work /work/data", msg)
            self.assertIn("stderr: bootstrap stderr", msg)
            self.assertIn("stdout: bootstrap stdout", msg)
        finally:
            self.bootstrap_patch.start()

    async def test_close_stops_gracefully_and_clears_runtime_state(self) -> None:
        """``close`` uses graceful stop and releases host-side handles."""
        workspace = DaytonaWorkspace(workspace_id="wid-10")
        await workspace.initialize()
        sandbox = workspace._sandbox
        gateway = workspace._gateway
        daytona_client = workspace._daytona

        await workspace.close()

        self.assertEqual(sandbox.stopped, [{"timeout": 60, "force": False}])
        self.assertTrue(gateway.closed)
        self.assertTrue(daytona_client.closed)
        self.assertIsNone(workspace._sandbox)
        self.assertIsNone(workspace._backend)
        self.assertIsNone(workspace._gateway)
        self.assertIsNone(workspace._daytona)
        self.assertFalse(workspace.is_alive)


async def _tool_text(tool: object, **kwargs: object) -> str:
    """Call a tool and return the concatenated text content."""
    result = await tool(**kwargs)
    chunks = []
    if hasattr(result, "__aiter__"):
        async for chunk in result:
            chunks.append(chunk)
    else:
        chunks.append(result)
    return "\n".join(
        block.text
        for chunk in chunks
        for block in chunk.content
        if hasattr(block, "text")
    )


class TestDaytonaWorkspaceBuiltinToolsMock(IsolatedAsyncioTestCase):
    """Exercise all six builtin tools through ``DaytonaBackend``."""

    async def asyncSetUp(self) -> None:
        """Create a mapped fake sandbox and initialized workspace."""
        _FakeDaytona.instances.clear()
        _FakeDaytona.list_result.clear()
        _FakeDaytona.created_params.clear()
        _FakeDaytona.configs.clear()
        _FakeGateway.instances.clear()
        _install_fake_daytona_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        sandbox = _MappedSandbox(self.temp_dir.name)
        sandbox.labels = {METADATA_WORKSPACE_ID_KEY: "wid-tools"}
        _FakeDaytona.list_result[:] = [sandbox]

        self.gateway_patch = patch.object(
            daytona_mod,
            "GatewayClient",
            _FakeGateway,
        )
        self.gateway_patch.start()
        self.bootstrap_patch = patch.object(
            DaytonaWorkspace,
            "_run_bootstrap",
            AsyncMock(),
        )
        self.bootstrap_patch.start()

        self.workspace = DaytonaWorkspace(workspace_id="wid-tools")
        await self.workspace.initialize()
        await self.workspace._backend.write_file(
            self.workspace._glob_helper_script,
            _read_glob_helper_bytes(),
        )
        self.tools = {
            tool.name: tool for tool in await self.workspace.list_tools()
        }

    async def asyncTearDown(self) -> None:
        """Close workspace and cleanup patches/temp files."""
        await self.workspace.close()
        self.bootstrap_patch.stop()
        self.gateway_patch.stop()
        sys.modules.pop("daytona", None)
        self.temp_dir.cleanup()

    async def test_all_builtin_tools_use_daytona_backend(self) -> None:
        """Bash, Write, Read, Edit, Grep and Glob operate in the sandbox."""
        bash_text = await _tool_text(
            self.tools["Bash"],
            command="printf 'alpha\\n'",
        )
        self.assertEqual(bash_text, "alpha\n")

        target = "/home/daytona/src/app.py"
        write_text = await _tool_text(
            self.tools["Write"],
            file_path=target,
            content="alpha\nbeta\n",
        )
        self.assertIn("written successfully", write_text)

        read_text = await _tool_text(self.tools["Read"], file_path=target)
        self.assertIn("alpha", read_text)
        self.assertIn("beta", read_text)

        edit_text = await _tool_text(
            self.tools["Edit"],
            file_path=target,
            old_string="beta",
            new_string="gamma",
        )
        self.assertIn("Successfully replaced", edit_text)

        grep_text = await _tool_text(
            self.tools["Grep"],
            pattern="gamma",
            path="/home/daytona/src",
            output_mode="content",
        )
        self.assertIn("/home/daytona/src/app.py", grep_text)
        self.assertIn("gamma", grep_text)

        glob_text = await _tool_text(
            self.tools["Glob"],
            pattern="**/*.py",
            path="/home/daytona/src",
        )
        self.assertIn("/home/daytona/src/app.py", glob_text)

    async def test_mcp_add_remove_persists_mcp_file(self) -> None:
        """Dynamic MCP changes are reflected in the sandbox ``.mcp`` file."""
        mcp = MCPClient(
            name="demo",
            mcp_config=StdioMCPConfig(command="node", args=["server.js"]),
            is_stateful=True,
        )

        await self.workspace.add_mcp(mcp)

        self.assertIn("demo", self.workspace._gateway_clients)
        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8"))[0]["name"], "demo")

        gw_client = self.workspace._gateway_clients["demo"]
        await self.workspace.remove_mcp("demo")

        self.assertTrue(gw_client.closed)
        self.assertNotIn("demo", self.workspace._gateway_clients)
        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8")), [])

    async def test_remove_missing_mcp_is_noop(self) -> None:
        """Removing an unknown MCP leaves persisted config unchanged."""
        await self.workspace.remove_mcp("missing")

        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8")), [])

    async def test_skill_add_list_remove_uses_sandbox_skills_dir(self) -> None:
        """Skill management stores and reads skills under SDK workdir."""
        skill_dir = os.path.join(self.temp_dir.name, "local-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: demo-skill\n"
                "description: Demo skill\n"
                "---\n"
                "Use this skill.\n",
            )

        await self.workspace.add_skill(skill_dir)
        skills = await self.workspace.list_skills()

        self.assertEqual([skill.name for skill in skills], ["demo-skill"])
        self.assertEqual(
            skills[0].dir,
            "/home/daytona/skills/local-skill",
        )

        await self.workspace.remove_skill("demo-skill")
        self.assertEqual(await self.workspace.list_skills(), [])

    async def test_add_skill_rejects_invalid_and_duplicate_skill(self) -> None:
        """Skill upload validates local input and duplicate remote dirs."""
        with self.assertRaisesRegex(ValueError, "SKILL.md not found"):
            await self.workspace.add_skill(
                os.path.join(self.temp_dir.name, "missing-skill"),
            )

        skill_dir = os.path.join(self.temp_dir.name, "dupe-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: dupe-skill\n"
                "description: Demo skill\n"
                "---\n"
                "Use this skill.\n",
            )

        await self.workspace.add_skill(skill_dir)
        with self.assertRaisesRegex(ValueError, "already exists"):
            await self.workspace.add_skill(skill_dir)

    async def test_offload_context_tool_result_and_reset(self) -> None:
        """Offload writes sessions/data and reset clears persistent state."""
        data_block = DataBlock(
            name="payload.txt",
            source=Base64Source(data="aGVsbG8=", media_type="text/plain"),
        )
        context_path = await self.workspace.offload_context(
            "session-1",
            [UserMsg(name="user", content=[TextBlock(text="see"), data_block])],
        )

        self.assertEqual(
            context_path,
            "/home/daytona/sessions/session-1/context.jsonl",
        )
        context_raw = await self.workspace._backend.read_file(context_path)
        self.assertIn("file:///home/daytona/data/", context_raw.decode("utf-8"))

        tool_path = await self.workspace.offload_tool_result(
            "session-1",
            ToolResultBlock(
                id="tool-1",
                name="tool",
                output=[
                    TextBlock(text="result"),
                    data_block,
                ],
                state=ToolResultState.SUCCESS,
            ),
        )
        self.assertEqual(
            tool_path,
            "/home/daytona/sessions/session-1/tool_result-tool-1.txt",
        )

        self.assertTrue(
            await self.workspace._backend.file_exists("/home/daytona/sessions"),
        )
        self.assertTrue(
            await self.workspace._backend.file_exists("/home/daytona/data"),
        )

        await self.workspace.reset()

        self.assertFalse(
            await self.workspace._backend.file_exists("/home/daytona/sessions"),
        )
        self.assertFalse(
            await self.workspace._backend.file_exists("/home/daytona/data"),
        )
        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8")), [])


def _mcp_server_script() -> bytes:
    """Minimal FastMCP stdio server used by live reattach tests."""
    return b"""from mcp.server import FastMCP

mcp = FastMCP("Persist")


@mcp.tool()
def ping() -> str:
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="stdio")
"""


def _make_live_agent(agent_id_suffix: str) -> AgentRecord:
    """Build a minimal agent record for live toolkit assembly."""
    return AgentRecord(
        user_id="daytona-live-user",
        source="user",
        data=AgentData(
            name=f"daytona-agent-{agent_id_suffix}",
            system_prompt="You operate inside the configured workspace.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _make_live_session(agent: AgentRecord, workspace_id: str) -> SessionRecord:
    """Build a minimal session record bound to the Daytona workspace."""
    return SessionRecord(
        user_id="daytona-live-user",
        agent_id=agent.id,
        config=SessionConfig(workspace_id=workspace_id),
    )


async def _call_tool(
    toolkit: object,
    state: AgentState,
    *,
    tool_call_id: str,
    name: str,
    tool_input: dict[str, object],
) -> ToolResponse:
    """Call a toolkit tool and return the final response."""
    response = None
    async for result in toolkit.call_tool(  # type: ignore[attr-defined]
        ToolCallBlock(
            id=tool_call_id,
            name=name,
            input=json.dumps(tool_input),
        ),
        state,
    ):
        if isinstance(result, ToolResponse):
            response = result
    assert response is not None
    return response


def _response_text(response: ToolResponse) -> str:
    """Flatten text blocks from a tool response."""
    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(parts)


@unittest.skipUnless(DAYTONA_API_KEY, SKIP_REASON)
class TestDaytonaWorkspaceLive(IsolatedAsyncioTestCase):
    """Live Daytona workspace coverage, skipped without credentials."""

    async def test_persistent_state_survives_close_and_reattach(self) -> None:
        """Live Daytona reattach preserves MCP, sessions, and data files."""
        workspace_id = live_daytona_workspace_id("daytona-live-persist")
        session_id = "session-live"
        mcp_name = "persist_mcp"

        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **live_daytona_kwargs(),
        )

        reattached = None
        try:
            await workspace.initialize()
            sandbox_id = workspace.sandbox_id
            data_block = DataBlock(
                name="payload.txt",
                source=Base64Source(
                    data="cmVhdHRhY2gtcGF5bG9hZA==",
                    media_type="text/plain",
                ),
            )
            context_path = await workspace.offload_context(
                session_id,
                [
                    UserMsg(
                        name="user",
                        content=[
                            TextBlock(text="persist context"),
                            data_block,
                        ],
                    ),
                ],
            )
            tool_path = await workspace.offload_tool_result(
                session_id,
                ToolResultBlock(
                    id="tool-live",
                    name="tool",
                    output=[
                        TextBlock(text="persist tool result"),
                        data_block,
                    ],
                    state=ToolResultState.SUCCESS,
                ),
            )

            mcp_script = f"{workspace.workdir}/persist_mcp.py"
            await workspace._backend.write_file(
                mcp_script,
                _mcp_server_script(),
            )
            await workspace.add_mcp(
                MCPClient(
                    name=mcp_name,
                    is_stateful=True,
                    mcp_config=StdioMCPConfig(
                        command=workspace._gateway_venv_py,
                        args=[mcp_script],
                    ),
                ),
            )
            self.assertIn(
                mcp_name,
                [mcp.name for mcp in await workspace.list_mcps()],
            )

            await workspace.close()

            reattached = DaytonaWorkspace(
                workspace_id=workspace_id,
                **live_daytona_kwargs(),
            )
            await reattached.initialize()

            self.assertEqual(reattached.sandbox_id, sandbox_id)
            context_raw = await reattached._backend.read_file(context_path)
            self.assertIn(
                "persist context",
                context_raw.decode("utf-8"),
            )
            self.assertIn(
                "file://",
                context_raw.decode("utf-8"),
            )
            tool_raw = await reattached._backend.read_file(tool_path)
            self.assertIn(
                "persist tool result",
                tool_raw.decode("utf-8"),
            )

            mcp_raw = await reattached._backend.read_file(
                f"{reattached.workdir}/.mcp",
            )
            self.assertEqual(
                json.loads(mcp_raw.decode("utf-8"))[0]["name"],
                mcp_name,
            )
            mcps = await reattached.list_mcps()
            self.assertIn(mcp_name, [mcp.name for mcp in mcps])
            raw_tools = await mcps[0].list_raw_tools()
            self.assertIn("ping", [tool.name for tool in raw_tools])
        finally:
            if reattached is not None:
                await reattached.close()
            else:
                await workspace.close()
            await delete_live_daytona_workspace(workspace_id)

    async def test_agent_toolkit_write_read_operates_in_sandbox(self) -> None:
        """Agent-facing toolkit tools operate through Daytona workspace."""
        workspace_id = live_daytona_workspace_id("daytona-live-agent")
        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **live_daytona_kwargs(),
        )
        try:
            await workspace.initialize()
            agent = _make_live_agent("toolkit")
            session = _make_live_session(agent, workspace.workspace_id)
            toolkit = await get_toolkit(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                workspace=workspace,
                scheduler_manager=SchedulerManager(
                    storage=_NoOpStorage(),  # type: ignore[arg-type]
                    message_bus=_NullBus(),  # type: ignore[arg-type]
                ),
                background_task_manager=BackgroundTaskManager(
                    message_bus=_NullBus(),  # type: ignore[arg-type]
                ),
                message_bus=_NullBus(),  # type: ignore[arg-type]
                user_id="daytona-live-user",
                agent_record=agent,
                session_record=session,
                extra_factory=None,
            )

            state = AgentState(session_id=session.id)
            state.permission_context.mode = PermissionMode.BYPASS
            file_path = f"{workspace.workdir}/agent-toolkit.txt"

            write_response = await _call_tool(
                toolkit,
                state,
                tool_call_id="write-live",
                name="Write",
                tool_input={
                    "file_path": file_path,
                    "content": "from agent toolkit\n",
                },
            )
            self.assertEqual(write_response.state, ToolResultState.SUCCESS)
            self.assertIn("written successfully", _response_text(write_response))

            read_response = await _call_tool(
                toolkit,
                state,
                tool_call_id="read-live",
                name="Read",
                tool_input={"file_path": file_path},
            )
            self.assertEqual(read_response.state, ToolResultState.SUCCESS)
            self.assertIn("from agent toolkit", _response_text(read_response))

            raw = await workspace._backend.read_file(file_path)
            self.assertEqual(raw, b"from agent toolkit\n")
        finally:
            await workspace.close()
            await delete_live_daytona_workspace(workspace_id)


if __name__ == "__main__":
    unittest.main()
