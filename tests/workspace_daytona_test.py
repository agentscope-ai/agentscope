# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaWorkspace`.

Most tests patch the Daytona SDK boundary so they run in normal CI.
Live tests are opt-in via ``DAYTONA_API_KEY``.
"""

import asyncio
import json
import os
import shlex
import shutil
import sys
import tempfile
import types
import unittest
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import Enum
from types import SimpleNamespace
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from _daytona_live_utils import (
    DAYTONA_API_KEY,
    SKIP_REASON,
    delete_live_daytona_workspace,
    live_daytona_kwargs,
    live_daytona_workspace_id,
)

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import BackgroundTaskManager, SchedulerManager
from agentscope.app._service import ResourceAccessService, get_toolkit
from agentscope.app.access import DenyAllResourceAccessPolicy
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
from agentscope.tool import ToolResponse
from agentscope.workspace import DaytonaBackend, DaytonaWorkspace
from agentscope.workspace import _sandboxed_base as sandboxed_mod
from agentscope.workspace._daytona._constants import (
    DEFAULT_GATEWAY_PORT,
    METADATA_WORKSPACE_ID_KEY,
)
from agentscope.workspace._utils import _GATEWAY_BASE_REQUIREMENTS


class _NullBus:
    """Message bus placeholder for service toolkit assembly."""


class _NoOpStorage:
    """Storage placeholder for tools that only bind the reference."""

    async def list_agents(self, _user_id: str) -> list[AgentRecord]:
        """No invitable agents in toolkit assembly tests."""
        return []


class _NoOpWorkspaceManager:
    """Workspace manager placeholder for toolkit assembly tests."""


class _FakeProcess:
    """Daytona process fake."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.fs: Any = None

    async def exec(self, command: str, **_kwargs: object) -> object:
        """Record command and return success."""
        self.commands.append(command)
        args = shlex.split(command)
        # ``DaytonaBackend`` appends a ``2>&1`` redirection to every
        # command; a real shell consumes it, so drop it before parsing.
        if args and args[-1] == "2>&1":
            args = args[:-1]
        exit_code = 0
        if self.fs is not None and args[:2] == ["mkdir", "-p"]:
            self.fs.dirs.update(args[2:])
        elif self.fs is not None and args[:2] == ["test", "-e"]:
            path = args[2]
            exit_code = 0 if self.fs.exists(path) else 1
        elif self.fs is not None and args[:2] == ["test", "-d"]:
            path = args[2]
            exit_code = 0 if path in self.fs.dirs else 1
        return SimpleNamespace(
            exit_code=exit_code,
            result="",
            artifacts=SimpleNamespace(stdout=""),
            additional_properties={},
        )


class _FakeFS:
    """Daytona fs fake."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def exists(self, path: str) -> bool:
        """Return whether a fake file or directory exists."""
        return path in self.files or path in self.dirs

    async def download_file(self, path: str) -> bytes:
        """Return stored file bytes."""
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def upload_file(self, file: bytes, remote_path: str) -> None:
        """Store uploaded bytes."""
        parent = os.path.dirname(remote_path)
        if parent:
            self.dirs.add(parent)
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
        self.created_at = f"2026-01-01T00:00:00Z-{sandbox_id}"
        self.updated_at: str | None = None
        self.last_activity_at: str | None = None
        self.fs = _FakeFS()
        self.process = _FakeProcess()
        self.process.fs = self.fs
        self.started = False
        self.recovered = False
        self.stopped: list[dict[str, object]] = []
        self.waited_for_stop = False

    async def get_work_dir(self) -> str:
        """Return SDK-derived workdir."""
        return self.workdir

    async def get_user_home_dir(self) -> str:
        """Return SDK-derived user home."""
        return self.user_home

    async def start(self, timeout: float | None = 60) -> None:
        """Mark sandbox started."""
        del timeout
        self.started = True
        self.state = "started"

    async def recover(self, timeout: float | None = 60) -> None:
        """Mark sandbox recovered."""
        del timeout
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
        del timeout

    async def wait_for_sandbox_stop(
        self,
        timeout: float | None = 60,
    ) -> None:
        """Mark stop wait."""
        del timeout
        self.waited_for_stop = True
        self.state = "stopped"

    async def refresh_data(self) -> None:
        """No-op refresh hook."""


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
        assert mapped_command is not None
        mapped_cwd = self._to_host(cwd) or self.host_root
        env = dict(os.environ)
        env["PATH"] = (
            os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
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
                artifacts=SimpleNamespace(stdout=""),
                additional_properties={},
            )
        stdout_text = self._to_sandbox(stdout.decode("utf-8", "replace"))
        stderr_text = self._to_sandbox(stderr.decode("utf-8", "replace"))
        result = stdout_text + stderr_text
        return SimpleNamespace(
            exit_code=proc.returncode,
            result=result,
            artifacts=SimpleNamespace(stdout=result),
            additional_properties={},
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

    async def create(
        self,
        params: object | None = None,
        **_kwargs: object,
    ) -> _FakeSandbox:
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

    async def list(
        self,
        query: object | None = None,
    ) -> AsyncIterator[_FakeSandbox]:
        """Return fake candidates, filtered by labels when possible."""
        labels = getattr(query, "labels", None)
        if labels:
            candidates = (
                sandbox
                for sandbox in _FakeDaytona.list_result
                if all(sandbox.labels.get(k) == v for k, v in labels.items())
            )
        else:
            candidates = iter(_FakeDaytona.list_result)
        for sandbox in candidates:
            yield sandbox

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


class _FakeDaytonaNotFoundError(Exception):
    """Fake Daytona structured not-found exception."""


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
    PAUSING = "pausing"
    PAUSED = "paused"
    RESUMING = "resuming"


class _FakeGateway:
    """GatewayClient fake."""

    instances: list["_FakeGateway"] = []

    def __init__(
        self,
        *,
        backend: DaytonaBackend,
        gateway_port: int,
        timeout: float | None = None,
        inline_limit: int | None = None,
        tmp_dir: str | None = None,
        gateway_log_path: str | None = None,
    ) -> None:
        self.backend = backend
        self.gateway_port = gateway_port
        self.timeout = timeout
        self.inline_limit = inline_limit
        self.tmp_dir = tmp_dir
        self.gateway_log_path = gateway_log_path
        self.closed = False
        _FakeGateway.instances.append(self)

    async def health(self) -> bool:
        """Always healthy."""
        return True

    async def list_mcps(self) -> list[MCPClient]:
        """Read persisted MCP specs from the fake sandbox."""
        path = self.backend.join_path(self.backend._workdir, ".mcp")
        try:
            raw = await self.backend.read_file(path)
        except FileNotFoundError:
            return []
        specs = json.loads(raw.decode("utf-8"))
        return [MCPClient.model_validate(spec) for spec in specs]

    async def aclose(self) -> None:
        """Mark closed."""
        self.closed = True

    def make_client(self, spec: dict[str, object]) -> MCPClient:
        """Build a regular MCP client from spec for add_mcp tests."""
        return _FakeGatewayMCPClient(spec)


class _FakeGatewayMCPClient:
    """Small gateway MCP client fake for add/remove tests."""

    def __init__(self, spec: dict[str, object]) -> None:
        self._spec = spec
        self.name = str(spec["name"])
        self.connected = False
        self.closed = False

    def model_dump(self, mode: str = "python") -> dict[str, object]:
        """Return the original MCP spec for persistence."""
        del mode
        return self._spec

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
    mod.DaytonaNotFoundError = _FakeDaytonaNotFoundError
    sys.modules["daytona"] = mod
    return mod


class TestDaytonaBootstrapHelpers(IsolatedAsyncioTestCase):
    """Bootstrap command rendering via ``_bootstrap_commands``."""

    @staticmethod
    def _workspace_with_paths(
        *,
        user_home: str,
        extra_pip: list[str],
    ) -> DaytonaWorkspace:
        """Build a workspace with the SDK-derived bootstrap paths set.

        Binds a backend so the base-class gateway path properties
        (``_gateway_venv`` / ``_gateway_python``) resolve without a live
        sandbox, then sets the SDK-derived anchors directly.
        """
        workspace = DaytonaWorkspace(
            workspace_id="wid-bootstrap",
            extra_pip=extra_pip,
        )
        workspace._backend = DaytonaBackend(None, workdir=user_home)
        workspace._user_home = user_home
        workspace._gateway_home = f"{user_home}/.agentscope"
        workspace._uv_bin = f"{user_home}/.local/bin/uv"
        return workspace

    async def test_bootstrap_commands_include_tools_and_extra_pip(
        self,
    ) -> None:
        """Bootstrap commands provision tools, venv and packages."""
        workspace = self._workspace_with_paths(
            user_home="/home/daytona",
            extra_pip=["extra-a", "extra-b"],
        )

        commands = workspace._bootstrap_commands()

        self.assertEqual(len(commands), 5)
        self.assertIn("apt-get install", commands[0])
        self.assertIn("ripgrep", commands[0])
        self.assertIn("UV_INSTALL_DIR=/home/daytona/.local/bin", commands[1])
        self.assertEqual(
            commands[2],
            "/home/daytona/.local/bin/uv venv "
            "/home/daytona/.agentscope/.venv",
        )
        for package in (*_GATEWAY_BASE_REQUIREMENTS, "extra-a", "extra-b"):
            self.assertIn(package, commands[3])
        self.assertIn("--no-deps 'agentscope'", commands[4])

    async def test_bootstrap_commands_quote_shell_arguments(self) -> None:
        """SDK-derived paths and extra packages are shell-quoted."""
        workspace = self._workspace_with_paths(
            user_home="/home/day tona",
            extra_pip=["safe-pkg", "bad; echo injected"],
        )

        commands = workspace._bootstrap_commands()

        self.assertIn(
            "UV_INSTALL_DIR='/home/day tona/.local/bin'",
            commands[1],
        )
        self.assertEqual(
            commands[2],
            "'/home/day tona/.local/bin/uv' venv "
            "'/home/day tona/.agentscope/.venv'",
        )
        self.assertIn("'bad; echo injected'", commands[3])


class _DaytonaWorkspaceMockBase(IsolatedAsyncioTestCase):
    """Shared fake-SDK fixture for Daytona workspace mock tests."""

    async def asyncSetUp(self) -> None:
        """Patch Daytona and gateway boundaries."""
        _FakeDaytona.instances.clear()
        _FakeDaytona.list_result.clear()
        _FakeDaytona.created_params.clear()
        _FakeDaytona.configs.clear()
        _FakeGateway.instances.clear()
        self.fake_daytona_mod = _install_fake_daytona_module()
        self.gateway_patch = patch.object(
            sandboxed_mod,
            "GatewayClient",
            _FakeGateway,
        )
        self.gateway_patch.start()
        self.bootstrap_patch = patch.object(
            DaytonaWorkspace,
            "_bootstrap_commands",
            return_value=[],
        )
        self.bootstrap_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.bootstrap_patch.stop()
        self.gateway_patch.stop()
        sys.modules.pop("daytona", None)


class TestDaytonaWorkspaceMock(_DaytonaWorkspaceMockBase):
    """Workspace lifecycle and configuration tests with a fake SDK."""

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

    async def test_create_passes_os_user_only_when_configured(self) -> None:
        """Explicit ``os_user`` is forwarded to Daytona create params."""
        workspace = DaytonaWorkspace(
            workspace_id="wid-os-user",
            os_user="daytona",
        )

        await workspace.initialize()

        params = _FakeDaytona.created_params[0]
        self.assertEqual(params.kwargs["os_user"], "daytona")

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

    async def test_initialize_reuses_stopped_candidate_and_starts_it(
        self,
    ) -> None:
        """Stopped candidates are started instead of creating a new one."""
        candidate = _FakeSandbox("sandbox-old", state="stopped")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-3"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-3")
        await workspace.initialize()

        self.assertIs(workspace._sandbox, candidate)
        self.assertTrue(candidate.started)
        self.assertEqual(_FakeDaytona.created_params, [])

    async def test_initialize_chooses_newest_duplicate_candidate(self) -> None:
        """Duplicate usable candidates choose newest and log a warning."""
        older = _FakeSandbox("sandbox-older", state="started")
        older.labels = {METADATA_WORKSPACE_ID_KEY: "wid-duplicates"}
        older.last_activity_at = "2026-01-01T00:00:00Z"
        newer = _FakeSandbox("sandbox-newer", state="started")
        newer.labels = {METADATA_WORKSPACE_ID_KEY: "wid-duplicates"}
        newer.last_activity_at = "2026-01-02T00:00:00Z"
        _FakeDaytona.list_result[:] = [older, newer]

        workspace = DaytonaWorkspace(workspace_id="wid-duplicates")
        with self.assertLogs("as", level="WARNING") as logs:
            await workspace.initialize()

        self.assertIs(workspace._sandbox, newer)
        self.assertEqual(_FakeDaytona.created_params, [])
        self.assertIn("2 sandboxes match", "\n".join(logs.output))

    async def test_initialize_waits_for_stopping_candidate_then_starts_it(
        self,
    ) -> None:
        """Stopping candidates are waited to stopped before start."""
        candidate = _FakeSandbox("sandbox-stopping", state="stopping")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-stopping"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-stopping")
        await workspace.initialize()

        self.assertTrue(candidate.waited_for_stop)
        self.assertTrue(candidate.started)

    async def test_initialize_waits_for_pausing_candidate_then_starts_it(
        self,
    ) -> None:
        """Pausing candidates are waited to stopped before start."""
        candidate = _FakeSandbox("sandbox-pausing", state="pausing")
        candidate.labels = {METADATA_WORKSPACE_ID_KEY: "wid-pausing"}
        _FakeDaytona.list_result[:] = [candidate]

        workspace = DaytonaWorkspace(workspace_id="wid-pausing")
        await workspace.initialize()

        self.assertIs(workspace._sandbox, candidate)
        self.assertTrue(candidate.waited_for_stop)
        self.assertTrue(candidate.started)
        self.assertEqual(_FakeDaytona.created_params, [])

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
        self.assertEqual(
            workspace._sessions_dir,
            "/workspace/project/sessions",
        )
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
            workspace._glob_helper_path,
            "/users/daytona/.agentscope/_glob_helper.py",
        )
        self.assertEqual(
            workspace._gateway_log,
            "/users/daytona/.agentscope/gateway.log",
        )
        self.assertEqual(workspace._uv_bin, "/users/daytona/.local/bin/uv")

    async def test_initialize_recovers_recoverable_error_candidate(
        self,
    ) -> None:
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

    async def test_initialize_skips_unrecoverable_error_candidate(
        self,
    ) -> None:
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

    async def test_gateway_uses_shared_backend_shim(self) -> None:
        """GatewayClient receives the Daytona backend, not a preview URL."""
        workspace = DaytonaWorkspace(
            workspace_id="wid-6",
            gateway_port=DEFAULT_GATEWAY_PORT,
        )

        await workspace.initialize()

        gateway = _FakeGateway.instances[0]
        self.assertIs(gateway.backend, workspace._backend)
        self.assertEqual(gateway.gateway_port, DEFAULT_GATEWAY_PORT)
        self.assertEqual(gateway.timeout, 30.0)

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
        self.assertEqual(
            json.loads(raw.decode("utf-8"))[0]["name"],
            "fallback",
        )

    async def test_initialize_bootstrap_error_includes_command_and_output(
        self,
    ) -> None:
        """Bootstrap failures include command, exit code, stderr and stdout."""
        self.bootstrap_patch.stop()
        try:

            class _FailingProcess(_FakeProcess):
                async def exec(
                    self,
                    command: str,
                    **_kwargs: object,
                ) -> object:
                    self.commands.append(command)
                    # Strip the ``2>&1`` redirection the backend appends.
                    args = shlex.split(command)
                    if args and args[-1] == "2>&1":
                        args = args[:-1]
                    if args == ["sh", "-c", "broken bootstrap"]:
                        return SimpleNamespace(
                            exit_code=9,
                            result="bootstrap stdout",
                            artifacts=SimpleNamespace(stdout=""),
                            additional_properties={},
                        )
                    return await super().exec(command, **_kwargs)

            candidate = _FakeSandbox("sandbox-bootstrap-error")
            candidate.labels = {
                METADATA_WORKSPACE_ID_KEY: "wid-bootstrap-error",
            }
            candidate.process = _FailingProcess()
            candidate.process.fs = candidate.fs
            _FakeDaytona.list_result[:] = [candidate]
            workspace = DaytonaWorkspace(workspace_id="wid-bootstrap-error")

            with patch.object(
                DaytonaWorkspace,
                "_bootstrap_commands",
                return_value=["broken bootstrap"],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "DaytonaWorkspace bootstrap failed",
                ) as cm:
                    await workspace.initialize()

            msg = str(cm.exception)
            self.assertIn("exit 9", msg)
            self.assertIn("broken bootstrap", msg)
            self.assertIn("stdout: bootstrap stdout", msg)
        finally:
            self.bootstrap_patch.start()

    async def test_close_stops_gracefully_and_clears_runtime_state(
        self,
    ) -> None:
        """``close`` uses graceful stop and releases host-side handles."""
        workspace = DaytonaWorkspace(workspace_id="wid-10")
        await workspace.initialize()
        sandbox = workspace._sandbox
        gateway = workspace._gateway
        daytona_client = workspace._daytona

        await workspace.close()

        self.assertEqual(sandbox.stopped, [{"timeout": 300, "force": False}])
        self.assertTrue(gateway.closed)
        self.assertTrue(daytona_client.closed)
        self.assertIsNone(workspace._sandbox)
        self.assertIsNone(workspace._backend)
        self.assertIsNone(workspace._gateway)
        self.assertIsNone(workspace._daytona)
        self.assertFalse(workspace.is_alive)


async def _tool_text(tool: Callable[..., Any], **kwargs: object) -> str:
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
        self.temp_dir = tempfile.mkdtemp()
        sandbox = _MappedSandbox(self.temp_dir)
        sandbox.labels = {METADATA_WORKSPACE_ID_KEY: "wid-tools"}
        _FakeDaytona.list_result[:] = [sandbox]

        self.gateway_patch = patch.object(
            sandboxed_mod,
            "GatewayClient",
            _FakeGateway,
        )
        self.gateway_patch.start()
        self.bootstrap_patch = patch.object(
            DaytonaWorkspace,
            "_bootstrap_commands",
            return_value=[],
        )
        self.bootstrap_patch.start()

        self.workspace = DaytonaWorkspace(workspace_id="wid-tools")
        await self.workspace.initialize()
        self.tools = {
            tool.name: tool for tool in await self.workspace.list_tools()
        }

    async def asyncTearDown(self) -> None:
        """Close workspace and cleanup patches/temp files."""
        await self.workspace.close()
        self.bootstrap_patch.stop()
        self.gateway_patch.stop()
        sys.modules.pop("daytona", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @unittest.skipIf(
        os.name == "nt",
        "host-backed Daytona fake relies on POSIX sandbox commands",
    )
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

        self.assertIn("demo", [m.name for m in self.workspace._mcps])
        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8"))[0]["name"], "demo")

        gw_client = next(m for m in self.workspace._mcps if m.name == "demo")
        await self.workspace.remove_mcp("demo")

        self.assertTrue(gw_client.closed)
        self.assertNotIn("demo", [m.name for m in self.workspace._mcps])
        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8")), [])

    async def test_remove_missing_mcp_is_noop(self) -> None:
        """Removing an unknown MCP leaves persisted config unchanged."""
        await self.workspace.remove_mcp("missing")

        raw = await self.workspace._backend.read_file("/home/daytona/.mcp")
        self.assertEqual(json.loads(raw.decode("utf-8")), [])

    @unittest.skipIf(
        os.name == "nt",
        "host-backed Daytona fake relies on POSIX sandbox commands",
    )
    async def test_skill_add_list_remove_uses_sandbox_skills_dir(self) -> None:
        """Skill management stores and reads skills under SDK workdir."""
        skill_dir = os.path.join(self.temp_dir, "local-skill")
        os.makedirs(skill_dir)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
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

    async def test_add_skill_rejects_invalid_skill(self) -> None:
        """Skill upload validates local input before sandbox work."""
        with self.assertRaisesRegex(ValueError, "SKILL.md not found"):
            await self.workspace.add_skill(
                os.path.join(self.temp_dir, "missing-skill"),
            )

    @unittest.skipIf(
        os.name == "nt",
        "host-backed Daytona fake relies on POSIX sandbox commands",
    )
    async def test_add_skill_rejects_duplicate_skill(self) -> None:
        """Skill upload rejects duplicate remote dirs."""
        skill_dir = os.path.join(self.temp_dir, "dupe-skill")
        os.makedirs(skill_dir)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
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
            [
                UserMsg(
                    name="user",
                    content=[TextBlock(text="see"), data_block],
                ),
            ],
        )

        self.assertEqual(
            context_path,
            "/home/daytona/sessions/session-1/context.jsonl",
        )
        context_raw = await self.workspace._backend.read_file(context_path)
        self.assertIn(
            "file:///home/daytona/data/",
            context_raw.decode("utf-8"),
        )

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
            await self.workspace._backend.file_exists(
                "/home/daytona/sessions",
            ),
        )
        self.assertTrue(
            await self.workspace._backend.file_exists("/home/daytona/data"),
        )

        await self.workspace.reset()

        self.assertFalse(
            await self.workspace._backend.file_exists(
                "/home/daytona/sessions",
            ),
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

    @asynccontextmanager
    async def _live_workspace(
        self,
        suffix: str,
        **kwargs: object,
    ) -> AsyncIterator[DaytonaWorkspace]:
        """Create one live workspace and guarantee best-effort cleanup."""
        workspace_id = live_daytona_workspace_id(suffix)
        daytona_kwargs = live_daytona_kwargs()
        daytona_kwargs.update(kwargs)
        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **daytona_kwargs,
        )
        try:
            await workspace.initialize()
            yield workspace
        finally:
            await workspace.close()
            await delete_live_daytona_workspace(workspace_id)

    def _create_live_skill(
        self,
        root: str,
        name: str,
        description: str,
        additional_files: dict[str, str] | None = None,
    ) -> str:
        """Create a local skill directory for live upload tests."""
        skill_dir = os.path.join(root, name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                f"---\nname: {name}\ndescription: {description}\n---\n\n"
                f"# {name}\n\n{description}\n",
            )
        for filename, content in (additional_files or {}).items():
            with open(
                os.path.join(skill_dir, filename),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(content)
        return skill_dir

    async def test_initialize_idempotent(self) -> None:
        """Calling ``initialize`` on a live workspace is a no-op."""
        async with self._live_workspace("daytona-live-init-idempotent") as ws:
            sandbox = ws._sandbox
            sandbox_id = ws.sandbox_id

            await ws.initialize()

            self.assertTrue(ws.is_alive)
            self.assertIs(ws._sandbox, sandbox)
            self.assertEqual(ws.sandbox_id, sandbox_id)

    async def test_close_marks_inactive(self) -> None:
        """``close`` flips ``is_alive`` and clears runtime handles."""
        workspace_id = live_daytona_workspace_id("daytona-live-close")
        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **live_daytona_kwargs(),
        )
        try:
            await workspace.initialize()
            self.assertTrue(workspace.is_alive)

            await workspace.close()

            self.assertFalse(workspace.is_alive)
            self.assertIsNone(workspace._sandbox)
            self.assertIsNone(workspace._backend)
        finally:
            await workspace.close()
            await delete_live_daytona_workspace(workspace_id)

    async def test_list_tools_builtin(self) -> None:
        """``list_tools`` returns the six builtin tools backed by Daytona."""
        from agentscope.tool._builtin import (
            Bash,
            Edit,
            Glob,
            Grep,
            Read,
            Write,
        )

        async with self._live_workspace("daytona-live-tools") as workspace:
            tools = await workspace.list_tools()

        self.assertEqual(len(tools), 6)
        self.assertSetEqual(
            {type(tool) for tool in tools},
            {Bash, Edit, Glob, Grep, Read, Write},
        )
        for tool in tools:
            self.assertIsInstance(tool._backend, DaytonaBackend)

    async def test_skill_paths_add_list_remove(self) -> None:
        """Skill paths and runtime skill changes work in a live sandbox."""
        with tempfile.TemporaryDirectory() as skill_root:
            seed_one = self._create_live_skill(
                skill_root,
                "live_seed_one",
                "First live skill",
                {"tool.py": "def run():\n    return 'one'\n"},
            )
            seed_two = self._create_live_skill(
                skill_root,
                "live_seed_two",
                "Second live skill",
            )

            async with self._live_workspace(
                "daytona-live-skills",
                skill_paths=[seed_one, seed_two],
            ) as workspace:
                seeded = sorted(
                    await workspace.list_skills(),
                    key=lambda skill: skill.name,
                )
                self.assertEqual(
                    [skill.name for skill in seeded],
                    ["live_seed_one", "live_seed_two"],
                )
                self.assertEqual(
                    [skill.description for skill in seeded],
                    ["First live skill", "Second live skill"],
                )
                self.assertEqual(
                    [skill.dir for skill in seeded],
                    [
                        f"{workspace.workdir}/skills/live_seed_one",
                        f"{workspace.workdir}/skills/live_seed_two",
                    ],
                )

                added = self._create_live_skill(
                    skill_root,
                    "live_added_skill",
                    "Added live skill",
                )
                await workspace.add_skill(added)
                skills = await workspace.list_skills()
                self.assertIn(
                    "live_added_skill",
                    [skill.name for skill in skills],
                )

                await workspace.remove_skill("live_added_skill")
                self.assertNotIn(
                    "live_added_skill",
                    [skill.name for skill in await workspace.list_skills()],
                )

    async def test_offload_context_tool_result_and_reset(self) -> None:
        """Live offload writes sessions/data and reset clears them."""
        async with self._live_workspace("daytona-live-offload-reset") as ws:
            data_block = DataBlock(
                name="payload.txt",
                source=Base64Source(data="aGVsbG8=", media_type="text/plain"),
            )
            context_path = await ws.offload_context(
                "session-live-reset",
                [
                    UserMsg(
                        name="user",
                        content=[TextBlock(text="live context"), data_block],
                    ),
                ],
            )
            context_raw = await ws._backend.read_file(context_path)
            self.assertIn("live context", context_raw.decode("utf-8"))
            self.assertIn("file://", context_raw.decode("utf-8"))

            tool_path = await ws.offload_tool_result(
                "session-live-reset",
                ToolResultBlock(
                    id="tool-live-reset",
                    name="tool",
                    output=[TextBlock(text="live tool result"), data_block],
                    state=ToolResultState.SUCCESS,
                ),
            )
            tool_raw = await ws._backend.read_file(tool_path)
            self.assertIn("live tool result", tool_raw.decode("utf-8"))
            self.assertTrue(await ws._backend.file_exists(ws._sessions_dir))
            self.assertTrue(await ws._backend.file_exists(ws._data_dir))

            await ws.reset()

            self.assertFalse(await ws._backend.file_exists(ws._sessions_dir))
            self.assertFalse(await ws._backend.file_exists(ws._data_dir))
            raw = await ws._backend.read_file(ws._mcp_file)
            self.assertEqual(json.loads(raw.decode("utf-8")), [])

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
                        command=workspace._gateway_python,
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
                workspace_manager=(
                    _NoOpWorkspaceManager()  # type: ignore[arg-type]
                ),
                scheduler_manager=SchedulerManager(
                    storage=_NoOpStorage(),  # type: ignore[arg-type]
                    message_bus=_NullBus(),  # type: ignore[arg-type]
                ),
                background_task_manager=BackgroundTaskManager(
                    message_bus=_NullBus(),  # type: ignore[arg-type]
                ),
                message_bus=_NullBus(),  # type: ignore[arg-type]
                middlewares=[],
                user_id="daytona-live-user",
                agent_record=agent,
                session_record=session,
                resource_access_service=ResourceAccessService(
                    storage=_NoOpStorage(),  # type: ignore[arg-type]
                    policy=DenyAllResourceAccessPolicy(),
                ),
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
            self.assertIn(
                "written successfully",
                _response_text(write_response),
            )

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
