# -*- coding: utf-8 -*-
# pylint: disable=protected-access,consider-using-with
"""Tests for Bubblewrap workspace and manager."""

from __future__ import annotations

import base64
import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import aiofiles
from fastapi.testclient import TestClient

from agentscope.app.workspace_manager import BubblewrapWorkspaceManager
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import Base64Source, DataBlock, UserMsg
from agentscope.tool import ExecResult
from agentscope.workspace import BubblewrapBackend, BubblewrapWorkspace
from agentscope.workspace._bubblewrap._constants import (
    BWRAP_SMOKE_PROBE_ARGV,
    SANDBOX_WORKDIR,
)
from agentscope.workspace._gateway_client import GatewayClient
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _State,
    _build_app,
)


def _bubblewrap_available() -> bool:
    """Return ``True`` iff ``bwrap`` can run a trivial command."""
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("bwrap") is None:
        return False
    try:
        result = subprocess.run(
            BWRAP_SMOKE_PROBE_ARGV,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_BWRAP_OK = _bubblewrap_available()
_SKIP_REASON = "Bubblewrap workspace requires Linux with a working bwrap"
_BWRAP_INTEGRATION_ENABLED = (
    os.getenv("AGENTSCOPE_BWRAP_INTEGRATION_TEST") == "1"
)
_INTEGRATION_SKIP_REASON = (
    _SKIP_REASON
    if _BWRAP_INTEGRATION_ENABLED
    else (
        "set AGENTSCOPE_BWRAP_INTEGRATION_TEST=1 to run Bubblewrap "
        "workspace integration tests"
    )
)


def _write_skill_dir(root: str, name: str, description: str) -> str:
    """Create a minimal skill directory."""
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
    return skill_dir


class TestBubblewrapWorkspaceInstructions(IsolatedAsyncioTestCase):
    """Pure local instruction rendering tests."""

    async def test_get_instructions_custom_template(self) -> None:
        """Custom instruction template gets the sandbox workdir."""
        ws = BubblewrapWorkspace(
            workspace_id="prompt-only",
            instructions="Workdir: {workdir}",
        )
        try:
            self.assertEqual(
                await ws.get_instructions(),
                f"Workdir: {SANDBOX_WORKDIR}",
            )
        finally:
            await ws.close()

    async def test_get_instructions_default_mentions_workdir(self) -> None:
        """Default template renders useful workspace details."""
        ws = BubblewrapWorkspace(workspace_id="prompt-default")
        try:
            text = await ws.get_instructions()
            self.assertIn("Bubblewrap-based Linux workspace", text)
            self.assertIn(SANDBOX_WORKDIR, text)
            self.assertIn("data/", text)
            self.assertIn("skills/", text)
            self.assertIn("sessions/", text)
        finally:
            await ws.close()

    async def test_gateway_port_defaults_to_dynamic(self) -> None:
        """No explicit gateway port is assigned until initialization."""
        ws1 = BubblewrapWorkspace(workspace_id="p1")
        ws2 = BubblewrapWorkspace(workspace_id="p2")
        try:
            self.assertIsNone(ws1.gateway_port)
            self.assertIsNone(ws2.gateway_port)
            port1 = ws1._allocate_gateway_port()
            port2 = ws2._allocate_gateway_port()
            self.assertIsInstance(port1, int)
            self.assertIsInstance(port2, int)
        finally:
            await ws1.close()
            await ws2.close()

    async def test_workspace_rejects_share_net_false(self) -> None:
        """Workspace-level TCP gateway currently requires shared network."""
        with self.assertRaises(ValueError):
            BubblewrapWorkspace(workspace_id="no-net", share_net=False)

    async def test_workspace_rejects_invalid_gateway_ports(self) -> None:
        """Fixed gateway ports must be valid TCP ports, not port zero."""
        for port in (0, -1, 65536, True):
            with self.assertRaises(ValueError):
                BubblewrapWorkspace(
                    workspace_id="invalid-port",
                    gateway_port=port,  # type: ignore[arg-type]
                )

    async def test_workspace_rejects_empty_workdir(self) -> None:
        """Explicit host workdir must not collapse to cwd."""
        with self.assertRaises(ValueError):
            BubblewrapWorkspace(workspace_id="empty", host_workdir="")

    async def test_ephemeral_workspace_is_never_persistent(self) -> None:
        """Ephemeral persistence is determined by constructor config."""
        ws = BubblewrapWorkspace(workspace_id="ephemeral")
        try:
            self.assertFalse(ws.is_persistent)
        finally:
            await ws.close()

    async def test_explicit_workdir_is_persistent(self) -> None:
        """Explicit host workdirs survive workspace close."""
        with tempfile.TemporaryDirectory() as workdir:
            ws = BubblewrapWorkspace(
                workspace_id="persistent",
                host_workdir=workdir,
            )
            try:
                self.assertTrue(ws.is_persistent)
            finally:
                await ws.close()

    async def test_initialize_failure_cleans_ephemeral_dirs(self) -> None:
        """Partial initialization failures clean owned host resources."""
        paths: dict[str, str] = {}

        class _FailingWorkspace(BubblewrapWorkspace):
            async def _provision_backend(self) -> None:
                """Create owned resources, then fail provisioning."""
                # pylint: disable=attribute-defined-outside-init
                self._owned_workdir = tempfile.TemporaryDirectory()
                self.host_workdir = self._owned_workdir.name
                self._tmpdir = tempfile.TemporaryDirectory()
                paths["workdir"] = self.host_workdir
                paths["tmpdir"] = self._tmpdir.name
                self._backend = BubblewrapBackend(
                    host_workdir=self.host_workdir,
                    host_tmpdir=self._tmpdir.name,
                )
                raise RuntimeError("boom")

        ws = _FailingWorkspace(workspace_id="fail-cleanup")

        with self.assertRaises(RuntimeError):
            await ws.initialize()

        self.assertFalse(os.path.exists(paths["workdir"]))
        self.assertFalse(os.path.exists(paths["tmpdir"]))

    async def test_probe_timeout_terminates_process_tree(self) -> None:
        """A timed-out smoke probe terminates the spawned bwrap process."""

        class _TimeoutProcess:
            returncode = None

            async def communicate(self) -> tuple[bytes, bytes]:
                """Pretend the subprocess communicate call timed out."""
                raise asyncio.TimeoutError()

        fake_process = _TimeoutProcess()

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=fake_process),
            ) as create_process,
            patch.object(
                BubblewrapBackend,
                "_terminate_process_tree",
                new=AsyncMock(),
            ) as terminate,
        ):
            with self.assertRaises(RuntimeError):
                await BubblewrapWorkspace._probe_bubblewrap()

        terminate.assert_awaited_once_with(fake_process, grace=1.0)
        kwargs = create_process.call_args.kwargs
        if os.name == "nt":
            self.assertNotIn("start_new_session", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])

    async def test_stop_gateway_process_terminates_tracked_process(
        self,
    ) -> None:
        """Gateway teardown terminates the tracked subprocess."""
        ws = BubblewrapWorkspace(workspace_id="stop-gateway")
        fake_process = AsyncMock()
        fake_process.returncode = None
        ws._gateway_process = fake_process

        async def _mark_terminated(
            process: Any,
            *,
            grace: float,
        ) -> None:
            """Simulate the process returncode being set after termination."""
            del grace
            process.returncode = -15

        with patch.object(
            BubblewrapBackend,
            "_terminate_process_tree",
            new=AsyncMock(side_effect=_mark_terminated),
        ) as terminate:
            await ws._stop_gateway_process()

        self.assertIsNone(ws._gateway_process)
        terminate.assert_awaited_once_with(fake_process, grace=5.0)

    async def test_stop_gateway_keeps_handle_when_termination_fails(
        self,
    ) -> None:
        """A failed termination keeps the process handle for retry."""
        ws = BubblewrapWorkspace(workspace_id="stop-failure")
        fake_process = AsyncMock()
        fake_process.returncode = None
        ws._gateway_process = fake_process

        with patch.object(
            BubblewrapBackend,
            "_terminate_process_tree",
            new=AsyncMock(side_effect=OSError("cannot terminate")),
        ):
            with self.assertRaises(OSError):
                await ws._stop_gateway_process()

        self.assertIs(ws._gateway_process, fake_process)

    async def test_bootstrap_uses_official_ripgrep_release(self) -> None:
        """Bootstrap installs rg from the official release asset."""
        ws = BubblewrapWorkspace(workspace_id="bootstrap")
        workdir = tempfile.TemporaryDirectory()
        tmpdir = tempfile.TemporaryDirectory()
        try:
            ws._backend = BubblewrapBackend(
                host_workdir=workdir.name,
                host_tmpdir=tmpdir.name,
            )
            commands = ws._bootstrap_commands()
            joined = "\n".join(commands)
            pip_commands = [cmd for cmd in commands if "uv pip install" in cmd]
            self.assertNotIn("ripgrep", "\n".join(pip_commands))
            self.assertIn("github.com/BurntSushi/ripgrep", joined)
            self.assertIn("sha256sum -c", joined)
            self.assertIn("sha256=f84757b0", joined)
            self.assertIn("sha256sum -c -", joined)
            self.assertIn('mktemp "${asset}.tmp.XXXXXX"', joined)
            self.assertIn('mv -f "$tmp_asset" "$asset"', joined)
            self.assertNotIn('"$asset.sha256"', joined)
            self.assertIn("tmp_installer=$(mktemp)", joined)
            self.assertIn('sh "$tmp_installer"', joined)
            self.assertNotIn("| env UV_INSTALL_DIR", joined)
        finally:
            workdir.cleanup()
            tmpdir.cleanup()
            await ws.close()

    async def test_gateway_credentials_rotate_per_launch(self) -> None:
        """Gateway credentials can be rotated for every launch attempt."""
        ws = BubblewrapWorkspace(workspace_id="rotate")
        try:
            first_token = ws._gateway_token
            first_nonce = ws._gateway_nonce

            ws._rotate_gateway_credentials()

            self.assertNotEqual(first_token, ws._gateway_token)
            self.assertNotEqual(first_nonce, ws._gateway_nonce)
        finally:
            await ws.close()

    async def test_gateway_client_passes_auth_token_to_shim(self) -> None:
        """Gateway requests include the optional auth token."""

        class _FakeGatewayBackend:
            command: list[str] | None = None

            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Capture the shim command and return a tiny response."""
                del cwd, timeout
                self.command = command
                body = base64.b64encode(b"ok").decode("ascii")
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        backend = _FakeGatewayBackend()
        client = GatewayClient(
            backend=backend,  # type: ignore[arg-type]
            gateway_port=5600,
            auth_token="secret-token",
        )
        self.assertEqual(
            await client.exec_request("GET", "/health"),
            (200, b"ok"),
        )
        self.assertIsNotNone(backend.command)
        command = backend.command
        assert command is not None
        self.assertEqual(command[-1], "secret-token")

    async def test_gateway_client_without_auth_token_passes_empty_arg(
        self,
    ) -> None:
        """Gateway requests without auth use an empty shim token arg."""

        class _FakeGatewayBackend:
            command: list[str] | None = None

            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Capture the shim command and return a tiny response."""
                del cwd, timeout
                self.command = command
                body = base64.b64encode(b"ok").decode("ascii")
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        backend = _FakeGatewayBackend()
        client = GatewayClient(
            backend=backend,  # type: ignore[arg-type]
            gateway_port=5600,
        )
        await client.exec_request("GET", "/health")

        command = backend.command
        assert command is not None
        self.assertEqual(command[-1], "")

    async def test_gateway_health_omits_auth_token(self) -> None:
        """Health probes do not send the bearer token."""

        class _FakeGatewayBackend:
            command: list[str] | None = None

            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Capture the health command and return plain ok."""
                del cwd, timeout
                self.command = command
                body = base64.b64encode(b"ok").decode("ascii")
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        backend = _FakeGatewayBackend()
        client = GatewayClient(
            backend=backend,  # type: ignore[arg-type]
            gateway_port=5600,
            auth_token="secret-token",
        )

        self.assertTrue(await client.health())
        command = backend.command
        assert command is not None
        self.assertEqual(command[-1], "")

    async def test_gateway_health_requires_expected_nonce(self) -> None:
        """Health succeeds only when the gateway nonce matches."""

        class _FakeGatewayBackend:
            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Return a health body with a different nonce."""
                del command, cwd, timeout
                payload = {"status": "ok", "instance_nonce": "actual"}
                body = base64.b64encode(json.dumps(payload).encode()).decode()
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        backend = _FakeGatewayBackend()
        mismatch = GatewayClient(
            backend=backend,  # type: ignore[arg-type]
            gateway_port=5600,
            instance_nonce="expected",
        )
        match = GatewayClient(
            backend=backend,  # type: ignore[arg-type]
            gateway_port=5600,
            instance_nonce="actual",
        )

        self.assertFalse(await mismatch.health())
        self.assertTrue(await match.health())

    async def test_gateway_health_rejects_non_object_json(self) -> None:
        """Nonce health checks reject valid JSON that is not an object."""

        class _FakeGatewayBackend:
            def __init__(self, body: bytes) -> None:
                self.body = body

            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Return a health body controlled by the test."""
                del command, cwd, timeout
                body = base64.b64encode(self.body).decode()
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        for body in (b"[]", b'"ok"', b"123", b"null"):
            client = GatewayClient(
                backend=_FakeGatewayBackend(body),  # type: ignore[arg-type]
                gateway_port=5600,
                instance_nonce="expected",
            )
            self.assertFalse(await client.health())

    async def test_gateway_health_rejects_non_ascii_nonce(self) -> None:
        """Nonce health checks reject non-ASCII response data safely."""

        class _FakeGatewayBackend:
            async def exec_shell(
                self,
                command: list[str],
                *,
                cwd: str | None = None,
                timeout: float | None = None,
            ) -> ExecResult:
                """Return a health body with a non-ASCII nonce."""
                del command, cwd, timeout
                payload = {"status": "ok", "instance_nonce": "\u00e9"}
                body = base64.b64encode(json.dumps(payload).encode()).decode()
                return ExecResult(
                    0,
                    json.dumps({"status": 200, "body": body}).encode(),
                    b"",
                )

        client = GatewayClient(
            backend=_FakeGatewayBackend(),  # type: ignore[arg-type]
            gateway_port=5600,
            instance_nonce="expected",
        )

        self.assertFalse(await client.health())


class TestBubblewrapWorkspaceCache(IsolatedAsyncioTestCase):
    """Pure local cache path tests."""

    async def test_workspace_rejects_empty_cache_dir(self) -> None:
        """Explicit cache dirs must not collapse to cwd."""
        with self.assertRaises(ValueError):
            BubblewrapWorkspace(workspace_id="empty-cache", host_cache_dir="")

    async def test_default_cache_dir_is_workspace_private(self) -> None:
        """Default caches are distinct and outside each workspace."""
        with tempfile.TemporaryDirectory() as parent:
            workdir1 = os.path.join(parent, "workspace-one")
            workdir2 = os.path.join(parent, "workspace-two")
            os.makedirs(workdir1)
            os.makedirs(workdir2)
            ws1 = BubblewrapWorkspace(
                workspace_id="cache-one",
                host_workdir=workdir1,
            )
            ws2 = BubblewrapWorkspace(
                workspace_id="cache-two",
                host_workdir=workdir2,
            )
            try:
                cache1 = ws1._resolve_host_cache_dir()
                cache2 = ws2._resolve_host_cache_dir()
            finally:
                await ws1.close()
                await ws2.close()

            self.assertNotEqual(cache1, cache2)
            self.assertNotEqual(
                os.path.commonpath([os.path.realpath(workdir1), cache1]),
                os.path.realpath(workdir1),
            )
            self.assertNotEqual(
                os.path.commonpath([os.path.realpath(workdir2), cache2]),
                os.path.realpath(workdir2),
            )
            self.assertEqual(
                os.path.basename(os.path.dirname(cache1)),
                ".agentscope-bwrap-cache",
            )

    async def test_explicit_cache_dir_is_opt_in_shared(self) -> None:
        """Shared writable caches only happen when requested explicitly."""
        with (
            tempfile.TemporaryDirectory() as workdir1,
            tempfile.TemporaryDirectory() as workdir2,
            tempfile.TemporaryDirectory() as cache_dir,
        ):
            ws1 = BubblewrapWorkspace(
                workspace_id="shared-cache-one",
                host_workdir=workdir1,
                host_cache_dir=cache_dir,
            )
            ws2 = BubblewrapWorkspace(
                workspace_id="shared-cache-two",
                host_workdir=workdir2,
                host_cache_dir=cache_dir,
            )
            try:
                expected_cache_dir = os.path.realpath(cache_dir)
                self.assertEqual(
                    ws1._resolve_host_cache_dir(),
                    expected_cache_dir,
                )
                self.assertEqual(
                    ws2._resolve_host_cache_dir(),
                    expected_cache_dir,
                )
            finally:
                await ws1.close()
                await ws2.close()

    async def test_cache_bind_source_cannot_overlap_workspace(self) -> None:
        """Cache bind sources cannot expose workspace paths or parents."""
        with tempfile.TemporaryDirectory() as parent:
            workdir = os.path.join(parent, "workspace")
            os.makedirs(workdir)
            for cache_dir in (
                os.path.join(workdir, "cache"),
                parent,
            ):
                ws = BubblewrapWorkspace(
                    workspace_id="unsafe-cache",
                    host_workdir=workdir,
                    host_cache_dir=cache_dir,
                )
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "must not overlap",
                    ):
                        ws._resolve_host_cache_dir()
                finally:
                    await ws.close()

    async def test_cache_bind_source_cannot_be_symlink(self) -> None:
        """Cache bind source roots must be real directories."""
        with (
            tempfile.TemporaryDirectory() as parent,
            tempfile.TemporaryDirectory() as outside,
        ):
            workdir = os.path.join(parent, "workspace")
            os.makedirs(workdir)
            cache_dir = os.path.join(parent, "cache-link")
            try:
                os.symlink(outside, cache_dir)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            ws = BubblewrapWorkspace(
                workspace_id="symlink-cache",
                host_workdir=workdir,
                host_cache_dir=cache_dir,
            )
            try:
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    ws._resolve_host_cache_dir()
            finally:
                await ws.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "POSIX mode test requires Linux",
    )
    async def test_new_explicit_workdir_uses_private_permissions(self) -> None:
        """A newly created explicit workdir uses mode 0700."""
        with tempfile.TemporaryDirectory() as parent:
            workdir = os.path.join(parent, "workspace")
            ws = BubblewrapWorkspace(
                workspace_id="private-workdir",
                host_workdir=workdir,
            )
            with (
                patch.object(
                    BubblewrapWorkspace,
                    "_probe_bubblewrap",
                    new=AsyncMock(),
                ),
                patch.object(shutil, "which", return_value="/usr/bin/bwrap"),
            ):
                await ws._provision_backend()
            try:
                mode = stat.S_IMODE(os.stat(workdir).st_mode)
                self.assertEqual(mode, 0o700)
            finally:
                await ws._teardown_backend()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Ephemeral provisioning requires Linux",
    )
    async def test_ephemeral_private_cache_is_cleaned_on_close(self) -> None:
        """An owned external cache is removed with an ephemeral workspace."""
        ws = BubblewrapWorkspace(workspace_id="ephemeral-cache")
        with (
            patch.object(
                BubblewrapWorkspace,
                "_probe_bubblewrap",
                new=AsyncMock(),
            ),
            patch.object(shutil, "which", return_value="/usr/bin/bwrap"),
        ):
            await ws._provision_backend()
        cache_dir = ws._host_cache_dir
        workdir = ws.host_workdir
        self.assertTrue(os.path.isdir(cache_dir))
        self.assertNotEqual(
            os.path.commonpath([cache_dir, workdir]),
            workdir,
        )

        await ws._teardown_backend()

        self.assertFalse(os.path.exists(cache_dir))
        self.assertFalse(os.path.exists(workdir))


class TestGatewayAuth(unittest.TestCase):
    """Pure FastAPI tests for optional gateway authentication."""

    def test_gateway_rejects_missing_token(self) -> None:
        """A protected gateway rejects missing auth."""
        client = TestClient(_build_app(_State(), auth_token="secret"))

        response = client.get("/mcps")

        self.assertEqual(response.status_code, 401)

    def test_gateway_rejects_wrong_token(self) -> None:
        """A protected gateway rejects incorrect auth."""
        client = TestClient(_build_app(_State(), auth_token="secret"))

        response = client.get(
            "/mcps",
            headers={"Authorization": "Bearer wrong"},
        )

        self.assertEqual(response.status_code, 401)

    def test_gateway_rejects_non_ascii_token(self) -> None:
        """Malformed non-ASCII auth headers return 401, not 500."""
        client = TestClient(_build_app(_State(), auth_token="secret"))

        response = client.get(
            "/mcps",
            headers=[
                (b"authorization", b"Bearer \xff"),
            ],
        )

        self.assertEqual(response.status_code, 401)

    def test_gateway_accepts_valid_token(self) -> None:
        """A protected gateway accepts the configured token."""
        client = TestClient(_build_app(_State(), auth_token="secret"))

        response = client.get(
            "/mcps",
            headers={"Authorization": "Bearer secret"},
        )

        self.assertEqual(response.status_code, 200)

    def test_gateway_auth_is_optional(self) -> None:
        """Unprotected gateway preserves existing backend behavior."""
        client = TestClient(_build_app(_State(), auth_token=None))

        response = client.get("/mcps")

        self.assertEqual(response.status_code, 200)

    def test_gateway_health_returns_nonce_without_auth(self) -> None:
        """Health exposes a nonce without requiring bearer auth."""
        client = TestClient(
            _build_app(
                _State(),
                auth_token="secret",
                instance_nonce="nonce",
            ),
        )

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["instance_nonce"], "nonce")


@unittest.skipUnless(
    _BWRAP_OK and _BWRAP_INTEGRATION_ENABLED,
    _INTEGRATION_SKIP_REASON,
)
class TestBubblewrapWorkspace(IsolatedAsyncioTestCase):
    """Integration tests against live Bubblewrap."""

    _shared_workdir: tempfile.TemporaryDirectory[str]
    _shared_cache: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        """Create one persistent workdir so bootstrap is paid once."""
        super().setUpClass()
        cls._shared_workdir = tempfile.TemporaryDirectory()
        cls._shared_cache = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop the shared persistent workdir."""
        cls._shared_cache.cleanup()
        cls._shared_workdir.cleanup()
        super().tearDownClass()

    async def asyncSetUp(self) -> None:
        """Create mounted dirs and a workspace."""
        # pylint: disable=consider-using-with
        self.skills_src = tempfile.TemporaryDirectory()
        self.workspace = BubblewrapWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self._shared_workdir.name,
            host_cache_dir=self._shared_cache.name,
        )
        await self.workspace.initialize()
        await self.workspace.reset()

    async def asyncTearDown(self) -> None:
        """Close workspace and drop scratch dirs."""
        try:
            if self.workspace.is_alive:
                await self.workspace.reset()
            await self.workspace.close()
        finally:
            self._clear_shared_state()
            self.skills_src.cleanup()

    def _clear_shared_state(self) -> None:
        """Remove user state while preserving the bootstrapped gateway."""
        for name in ("data", "sessions", "skills", ".mcp"):
            path = os.path.join(self._shared_workdir.name, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)

    async def test_initialize_gateway_and_tools(self) -> None:
        """Initialization starts gateway and returns builtins."""
        self.assertTrue(self.workspace.is_alive)
        self.assertListEqual(await self.workspace.list_mcps(), [])
        tools = await self.workspace.list_tools()
        self.assertSetEqual(
            {tool.name for tool in tools},
            {"Bash", "Edit", "Glob", "Grep", "Read", "Write"},
        )
        for tool in tools:
            self.assertIsInstance(tool._backend, BubblewrapBackend)

    async def test_persistent_workspace_repairs_partial_bootstrap(
        self,
    ) -> None:
        """A damaged persisted venv is repaired on the next initialization."""
        workdir = tempfile.TemporaryDirectory()
        cache_dir = tempfile.TemporaryDirectory()
        first = BubblewrapWorkspace(
            workspace_id=f"repair-first-{uuid.uuid4().hex[:8]}",
            host_workdir=workdir.name,
            host_cache_dir=cache_dir.name,
        )
        second: BubblewrapWorkspace | None = None
        try:
            await first.initialize()
            await first.close()

            python_path = os.path.join(
                workdir.name,
                ".agentscope",
                ".venv",
                "bin",
                "python",
            )
            os.remove(python_path)

            second = BubblewrapWorkspace(
                workspace_id=f"repair-second-{uuid.uuid4().hex[:8]}",
                host_workdir=workdir.name,
                host_cache_dir=cache_dir.name,
            )
            await second.initialize()
            self.assertTrue(second.is_alive)
            result = await second.get_backend().exec_shell(
                ["python", "--version"],
            )
            self.assertTrue(result.ok(), result.stderr.decode())
        finally:
            if second is not None:
                await second.close()
            await first.close()
            cache_dir.cleanup()
            workdir.cleanup()

    async def test_bootstrap_tools_available(self) -> None:
        """User-space bootstrap exposes uv and rg in PATH."""
        uv = await self.workspace.get_backend().exec_shell(["uv", "--version"])
        rg = await self.workspace.get_backend().exec_shell(["rg", "--version"])
        self.assertTrue(uv.ok(), uv.stderr.decode(errors="replace"))
        self.assertTrue(rg.ok(), rg.stderr.decode(errors="replace"))

    async def test_offload_context_and_datablock(self) -> None:
        """Offload writes session JSONL and decoded data."""
        b64_data = base64.b64encode(b"hello-data").decode()
        msg = UserMsg(
            name="user",
            content=[
                DataBlock(
                    source=Base64Source(
                        data=b64_data,
                        media_type="text/plain",
                    ),
                    name="note.txt",
                ),
            ],
        )
        path = await self.workspace.offload_context("s1", [msg])
        self.assertEqual(path, f"{SANDBOX_WORKDIR}/sessions/s1/context.jsonl")

        host_path = os.path.join(
            self._shared_workdir.name,
            "sessions",
            "s1",
            "context.jsonl",
        )
        async with aiofiles.open(host_path, "r") as f:
            content = await f.read()
        self.assertIn("file:///workspace/data/", content)
        self.assertTrue(
            os.path.isdir(os.path.join(self._shared_workdir.name, "data")),
        )

    async def test_skills_crud(self) -> None:
        """Skill add/list/remove works through the backend."""
        skill_path = _write_skill_dir(
            self.skills_src.name,
            "greeter",
            "Says hi.",
        )
        self.assertListEqual(await self.workspace.list_skills(), [])
        await self.workspace.add_skill(skill_path)
        skills = await self.workspace.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "greeter")
        self.assertEqual(skills[0].dir, f"{SANDBOX_WORKDIR}/skills/greeter")

        await self.workspace.remove_skill("greeter")
        self.assertListEqual(await self.workspace.list_skills(), [])

    async def test_reset_clears_state_but_keeps_gateway(self) -> None:
        """Reset clears user state while keeping the gateway usable."""
        await self.workspace.offload_context(
            "reset",
            [UserMsg(name="user", content="hi")],
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self._shared_workdir.name, "sessions"),
            ),
        )

        await self.workspace.reset()
        self.assertFalse(
            os.path.exists(
                os.path.join(self._shared_workdir.name, "sessions"),
            ),
        )
        self.assertListEqual(await self.workspace.list_mcps(), [])

    async def test_close_is_idempotent(self) -> None:
        """Closing twice does not raise."""
        await self.workspace.close()
        await self.workspace.close()

    async def test_close_terminates_gateway_process(self) -> None:
        """Closing a live workspace terminates its gateway process."""
        process = self.workspace._gateway_process
        self.assertIsNotNone(process)

        await self.workspace.close()

        await asyncio.wait_for(process.wait(), timeout=5.0)
        self.assertIsNotNone(process.returncode)

    async def test_workdir_persistence_across_restart(self) -> None:
        """Same host workdir preserves state across workspace instances."""
        msg = UserMsg(name="user", content="durable")
        await self.workspace.offload_context("persist", [msg])
        await self.workspace.close()

        ws2 = BubblewrapWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self._shared_workdir.name,
            host_cache_dir=self._shared_cache.name,
        )
        try:
            await ws2.initialize()
            host_path = os.path.join(
                self._shared_workdir.name,
                "sessions",
                "persist",
                "context.jsonl",
            )
            async with aiofiles.open(host_path, "r") as f:
                self.assertEqual(
                    (await f.read()).strip(),
                    msg.model_dump_json(),
                )
        finally:
            await ws2.close()

    @unittest.skipUnless(
        os.getenv("AGENTSCOPE_BWRAP_NETWORK_TEST") == "1",
        "set AGENTSCOPE_BWRAP_NETWORK_TEST=1 to run network MCP test",
    )
    async def test_optional_network_mcp(self) -> None:
        """Register and query a pure-Python MCP via uvx."""
        mcp_client = MCPClient(
            name="time",
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="uvx",
                args=["mcp-server-time"],
            ),
        )
        await self.workspace.add_mcp(mcp_client)
        mcps = await self.workspace.list_mcps()
        self.assertEqual(len(mcps), 1)
        tools = await mcps[0].list_raw_tools()
        self.assertGreater(len(tools), 0)
        tool = await mcps[0].get_tool("get_current_time")
        result = await tool(timezone="Asia/Tokyo")
        self.assertIsNotNone(result)


@unittest.skipUnless(
    _BWRAP_OK and _BWRAP_INTEGRATION_ENABLED,
    _INTEGRATION_SKIP_REASON,
)
class TestBubblewrapWorkspaceConcurrency(IsolatedAsyncioTestCase):
    """Concurrency scenarios that need multiple live workspaces."""

    async def asyncSetUp(self) -> None:
        """Create a shared bootstrap cache for live concurrency tests."""
        # pylint: disable=consider-using-with
        self.cache_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Drop the shared bootstrap cache."""
        self.cache_dir.cleanup()

    async def test_two_workspaces_can_start_concurrently(self) -> None:
        """Dynamic gateway ports avoid shared-loopback collisions."""
        # pylint: disable=consider-using-with
        workdir1 = tempfile.TemporaryDirectory()
        workdir2 = tempfile.TemporaryDirectory()
        ws1 = BubblewrapWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=workdir1.name,
            host_cache_dir=self.cache_dir.name,
        )
        ws2 = BubblewrapWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=workdir2.name,
            host_cache_dir=self.cache_dir.name,
        )
        try:
            await asyncio.gather(ws1.initialize(), ws2.initialize())
            self.assertIsInstance(ws1.gateway_port, int)
            self.assertIsInstance(ws2.gateway_port, int)
            self.assertNotEqual(ws1.gateway_port, ws2.gateway_port)

            rogue = GatewayClient(
                backend=ws1.get_backend(),
                gateway_port=ws2.gateway_port,
                timeout=5.0,
                instance_nonce=ws1._gateway_nonce,
            )
            self.assertFalse(await rogue.health())
            self.assertTrue(await ws2._gateway.health())
        finally:
            await asyncio.gather(
                ws1.close(),
                ws2.close(),
                return_exceptions=True,
            )
            workdir1.cleanup()
            workdir2.cleanup()

    async def test_ephemeral_workspace_reinitialize_cleans_new_dirs(
        self,
    ) -> None:
        """An ephemeral workspace can close and initialize again cleanly."""
        ws = BubblewrapWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_cache_dir=self.cache_dir.name,
        )
        try:
            await ws.initialize()
            first = ws.host_workdir
            await ws.close()
            self.assertFalse(os.path.exists(first))

            await ws.initialize()
            second = ws.host_workdir
            self.assertNotEqual(first, second)
            await ws.close()
            self.assertFalse(os.path.exists(second))
        finally:
            await ws.close()


class _FakeWorkspace:
    """Tiny workspace double for manager cache tests."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self.closed = False

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


class _FakeBubblewrapWorkspaceManager(BubblewrapWorkspaceManager):
    """Manager that avoids starting real Bubblewrap workspaces."""

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> Any:
        del user_id, agent_id
        return _FakeWorkspace(workspace_id)


class TestBubblewrapWorkspaceManager(IsolatedAsyncioTestCase):
    """Manager cache behavior tests that do not require bwrap."""

    async def asyncSetUp(self) -> None:
        """Create a manager rooted at a temp dir."""
        # pylint: disable=consider-using-with
        self.basedir = tempfile.TemporaryDirectory()
        self.manager = _FakeBubblewrapWorkspaceManager(
            self.basedir.name,
            ttl=0.01,
            sweep_interval=60.0,
        )

    async def asyncTearDown(self) -> None:
        """Close manager and scratch dir."""
        await self.manager.close_all()
        self.basedir.cleanup()

    async def test_get_workspace_reuses_explicit_id(self) -> None:
        """Same explicit workspace id returns cached object."""
        ws1 = await self.manager.get_workspace("u", "a", "s", "wid")
        ws2 = await self.manager.get_workspace("u", "a", "s", "wid")
        self.assertIs(ws1, ws2)

    async def test_close_evicts_workspace(self) -> None:
        """Closing one id evicts and closes it."""
        ws = await self.manager.get_workspace("u", "a", "s", "wid")
        await self.manager.close("wid")
        self.assertTrue(ws.closed)
        self.assertNotIn("wid", self.manager._cache)

    async def test_close_all_closes_cached_workspaces(self) -> None:
        """``close_all`` closes every cached workspace."""
        ws1 = await self.manager.get_workspace("u", "a", "s", "one")
        ws2 = await self.manager.get_workspace("u", "a", "s", "two")
        await self.manager.close_all()
        self.assertTrue(ws1.closed)
        self.assertTrue(ws2.closed)
        self.assertDictEqual(self.manager._cache, {})

    async def test_create_workspace_always_creates_new_workspace(self) -> None:
        """Deprecated create still creates a fresh workspace each call."""
        ws1 = await self.manager.create_workspace("u", "a", "s1")
        ws2 = await self.manager.create_workspace("u", "a", "s2")

        self.assertNotEqual(ws1.workspace_id, ws2.workspace_id)
        self.assertFalse(ws1.closed)
        self.assertFalse(ws2.closed)
        self.assertIn(ws1.workspace_id, self.manager._cache)
        self.assertIn(ws2.workspace_id, self.manager._cache)

    async def test_sweep_once_evicts_expired(self) -> None:
        """Sweeper evicts entries older than ttl."""
        ws = await self.manager.get_workspace("u", "a", "s", "wid")
        self.manager._cache["wid"] = (ws, time.monotonic() - 1.0)
        await self.manager._sweep_once()
        self.assertTrue(ws.closed)
        self.assertNotIn("wid", self.manager._cache)

    async def test_manager_rejects_share_net_false(self) -> None:
        """Manager mirrors workspace network limitation."""
        with self.assertRaises(ValueError):
            _FakeBubblewrapWorkspaceManager(
                self.basedir.name,
                share_net=False,
            )

    async def test_manager_rejects_invalid_gateway_port(self) -> None:
        """Manager validates gateway ports before creating workspaces."""
        with self.assertRaises(ValueError):
            _FakeBubblewrapWorkspaceManager(
                self.basedir.name,
                gateway_port=0,
            )

    async def test_manager_rejects_empty_basedir(self) -> None:
        """Manager basedir must not collapse to cwd."""
        with self.assertRaises(ValueError):
            _FakeBubblewrapWorkspaceManager("")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits only")
    async def test_manager_workdir_is_private(self) -> None:
        """Manager-created workdirs use 0700 permissions."""
        manager = BubblewrapWorkspaceManager(
            self.basedir.name,
            ttl=0.01,
            sweep_interval=60.0,
        )
        try:
            with patch.object(
                BubblewrapWorkspace,
                "initialize",
                new=AsyncMock(),
            ):
                ws = await manager._build_and_start(
                    workspace_id="wid",
                    user_id="u",
                    agent_id="a",
                )
            mode = os.stat(ws.host_workdir).st_mode & 0o777
            self.assertEqual(mode, 0o700)
        finally:
            await manager.close_all()

    async def test_workdir_tracks_workspace_id(self) -> None:
        """Different workspace ids use different host workdirs."""
        one = self.manager._workdir_for("user", "workspace-one")
        two = self.manager._workdir_for("user", "workspace-two")
        self.assertNotEqual(one, two)
        self.assertEqual(
            os.path.commonpath(
                [os.path.realpath(self.basedir.name), os.path.realpath(one)],
            ),
            os.path.realpath(self.basedir.name),
        )

    async def test_workdir_hashes_unsafe_identifiers(self) -> None:
        """External ids cannot escape the manager basedir."""
        for user_id, workspace_id in (
            ("u", "../../outside"),
            ("u", os.path.abspath(os.sep)),
            ("../outside", "wid"),
        ):
            path = self.manager._workdir_for(user_id, workspace_id)
            self.assertEqual(
                os.path.commonpath(
                    [
                        os.path.realpath(self.basedir.name),
                        os.path.realpath(path),
                    ],
                ),
                os.path.realpath(self.basedir.name),
            )


if __name__ == "__main__":
    unittest.main()
