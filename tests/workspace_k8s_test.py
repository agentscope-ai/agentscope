# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for K8sWorkspace, K8sBackend, and K8sWorkspaceManager.

All tests use mocked ``kubernetes_asyncio`` APIs — no real K8s cluster
is required.
"""

import io
import tarfile
import unittest
from collections.abc import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.workspace._k8s._k8s_bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    GATEWAY_HOME,
    GATEWAY_SCRIPT,
    GATEWAY_VENV_PY,
    POD_DATA_DIR,
    POD_MCP_FILE,
    POD_SESSIONS_DIR,
    POD_SKILLS_DIR,
    POD_WORKDIR,
    SYSTEM_DEPS,
    _k8s_safe_name,
    bootstrap_commands,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)

# ── _k8s_safe_name tests ──────────────────────────────────────────


class TestK8sSafeName(unittest.TestCase):
    """Validate RFC-1123 name sanitisation."""

    def test_uuid_hex_passthrough(self) -> None:
        """A standard uuid4().hex (lowercase hex) passes through."""
        wid = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        name = _k8s_safe_name(wid)
        self.assertTrue(name.startswith("as-ws-"))
        self.assertEqual(name, f"as-ws-{wid}")

    def test_uppercase_lowered(self) -> None:
        """Uppercase characters are lowered."""
        name = _k8s_safe_name("ABC123")
        self.assertEqual(name, "as-ws-abc123")

    def test_special_chars_replaced(self) -> None:
        """Underscores and dots are replaced with hyphens."""
        name = _k8s_safe_name("my_ws.v2")
        self.assertEqual(name, "as-ws-my-ws-v2")

    def test_truncation(self) -> None:
        """Names longer than 63 chars are truncated."""
        wid = "a" * 100
        name = _k8s_safe_name(wid)
        self.assertLessEqual(len(name), 63)

    def test_trailing_hyphen_stripped(self) -> None:
        """Trailing hyphens from truncation are removed."""
        wid = "a" * 56 + "---"
        name = _k8s_safe_name(wid)
        self.assertFalse(name.endswith("-"))

    def test_custom_prefix(self) -> None:
        """Custom prefix is applied."""
        name = _k8s_safe_name("test", prefix="pvc-")
        self.assertEqual(name, "pvc-test")


# ── bootstrap_commands tests ──────────────────────────────────────


class TestBootstrapCommands(unittest.TestCase):
    """Validate the bootstrap command sequence."""

    def test_basic_sequence_length(self) -> None:
        """Bootstrap generates exactly 6 commands."""
        cmds = bootstrap_commands(
            install_agentscope_cmd="echo installed",
        )
        self.assertEqual(len(cmds), 6)

    def test_mkdir_is_first(self) -> None:
        """First command creates the persistent directory layout."""
        cmds = bootstrap_commands(
            install_agentscope_cmd="echo installed",
        )
        self.assertIn("mkdir -p", cmds[0])
        self.assertIn(POD_DATA_DIR, cmds[0])
        self.assertIn(POD_SKILLS_DIR, cmds[0])
        self.assertIn(POD_SESSIONS_DIR, cmds[0])

    def test_system_deps_installed(self) -> None:
        """Second command installs system dependencies."""
        cmds = bootstrap_commands(
            install_agentscope_cmd="echo installed",
        )
        for dep in SYSTEM_DEPS:
            self.assertIn(dep, cmds[1])

    def test_uv_install(self) -> None:
        """Third command installs uv."""
        cmds = bootstrap_commands(
            install_agentscope_cmd="echo installed",
        )
        self.assertIn("astral.sh/uv/install.sh", cmds[2])

    def test_extra_pip_included(self) -> None:
        """Extra pip packages appear in the venv install command."""
        cmds = bootstrap_commands(
            extra_pip=["numpy", "pandas"],
            install_agentscope_cmd="echo installed",
        )
        self.assertIn("numpy", cmds[4])
        self.assertIn("pandas", cmds[4])

    def test_agentscope_cmd_forwarded(self) -> None:
        """The agentscope install command is the last command."""
        cmds = bootstrap_commands(
            install_agentscope_cmd="uv pip install agentscope",
        )
        self.assertEqual(cmds[5], "uv pip install agentscope")


class TestRenderInstallCommands(unittest.TestCase):
    """Validate install command rendering."""

    def test_released_cmd(self) -> None:
        """Released mode pins the version."""
        cmd = render_install_agentscope_cmd_released("1.2.3")
        self.assertIn("agentscope==1.2.3", cmd)
        self.assertIn("--no-deps", cmd)

    def test_dev_cmd(self) -> None:
        """Dev mode untars and installs from source."""
        cmd = render_install_agentscope_cmd_dev()
        self.assertIn("tar -xf", cmd)
        self.assertIn("--no-deps", cmd)


# ── K8sBackend tests ──────────────────────────────────────────────


class TestK8sBackendExecShell(IsolatedAsyncioTestCase):
    """Test K8sBackend.exec_shell with mocked K8s API."""

    async def test_exec_shell_wraps_cwd(self) -> None:
        """exec_shell wraps command with cd <cwd>."""
        from agentscope.workspace._k8s._k8s_backend import K8sBackend

        mock_api_client = MagicMock()
        mock_api_client.configuration = MagicMock()

        backend = K8sBackend(
            api_client=mock_api_client,
            namespace="default",
            pod_name="test-pod",
            container_name="workspace",
            workdir="/workspace",
        )

        mock_v1 = MagicMock()
        mock_sock = AsyncMock()
        mock_sock.__aenter__ = AsyncMock(return_value=mock_sock)
        mock_sock.__aexit__ = AsyncMock(return_value=False)

        async def _empty_aiter(
            _self: object,
        ) -> "AsyncGenerator[None, None]":
            return
            yield  # make it an async generator

        mock_sock.__aiter__ = _empty_aiter
        mock_v1.connect_get_namespaced_pod_exec = AsyncMock(
            return_value=mock_sock,
        )

        mock_ws_api = AsyncMock()
        mock_ws_api.__aenter__ = AsyncMock(return_value=mock_ws_api)
        mock_ws_api.__aexit__ = AsyncMock(return_value=False)

        mock_core_v1 = MagicMock(return_value=mock_v1)

        with (
            patch(
                "kubernetes_asyncio.stream.WsApiClient",
                return_value=mock_ws_api,
            ),
            patch(
                "kubernetes_asyncio.client.CoreV1Api",
                mock_core_v1,
            ),
        ):
            await backend.exec_shell(
                ["ls", "-la"],
                cwd="/tmp",
            )

        call_args = mock_v1.connect_get_namespaced_pod_exec.call_args
        cmd = call_args.kwargs.get("command")
        self.assertIn("/tmp", cmd[2])

    async def test_exec_shell_timeout(self) -> None:
        """exec_shell returns exit_code=-1 on timeout."""
        from agentscope.workspace._k8s._k8s_backend import K8sBackend

        mock_api_client = MagicMock()
        mock_api_client.configuration = MagicMock()

        backend = K8sBackend(
            api_client=mock_api_client,
            namespace="default",
            pod_name="test-pod",
            container_name="workspace",
            workdir="/workspace",
        )

        mock_v1 = MagicMock()

        async def hang_forever(
            *_args: object,
            **_kwargs: object,
        ) -> None:
            import asyncio

            await asyncio.sleep(100)

        mock_sock = AsyncMock()
        mock_sock.__aenter__ = AsyncMock(side_effect=hang_forever)
        mock_v1.connect_get_namespaced_pod_exec = AsyncMock(
            return_value=mock_sock,
        )

        mock_ws_api = AsyncMock()
        mock_ws_api.__aenter__ = AsyncMock(return_value=mock_ws_api)
        mock_ws_api.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "kubernetes_asyncio.stream.WsApiClient",
                return_value=mock_ws_api,
            ),
            patch(
                "kubernetes_asyncio.client.CoreV1Api",
                return_value=mock_v1,
            ),
        ):
            result = await backend.exec_shell(
                ["sleep", "100"],
                timeout=0.1,
            )

        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"timed out", result.stderr)


class TestK8sBackendReadFile(IsolatedAsyncioTestCase):
    """Test K8sBackend.read_file (tar extraction)."""

    async def test_read_file_extracts_tar(self) -> None:
        """read_file correctly extracts content from a tar stream."""
        from agentscope.workspace._k8s._k8s_backend import K8sBackend

        expected_content = b"hello world from pod"

        # Build a tar containing the expected file
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name="test.txt")
            info.size = len(expected_content)
            tf.addfile(info, io.BytesIO(expected_content))
        tar_bytes = buf.getvalue()

        mock_api_client = MagicMock()
        mock_api_client.configuration = MagicMock()

        backend = K8sBackend(
            api_client=mock_api_client,
            namespace="default",
            pod_name="test-pod",
            container_name="workspace",
            workdir="/workspace",
        )

        from agentscope.tool._builtin._backend import ExecResult

        with patch.object(
            backend,
            "exec_shell",
            new=AsyncMock(
                return_value=ExecResult(
                    exit_code=0,
                    stdout=tar_bytes,
                    stderr=b"",
                ),
            ),
        ):
            content = await backend.read_file("/workspace/test.txt")

        self.assertEqual(content, expected_content)

    async def test_read_file_not_found(self) -> None:
        """read_file raises FileNotFoundError for missing files."""
        from agentscope.workspace._k8s._k8s_backend import K8sBackend

        mock_api_client = MagicMock()
        mock_api_client.configuration = MagicMock()

        backend = K8sBackend(
            api_client=mock_api_client,
            namespace="default",
            pod_name="test-pod",
            container_name="workspace",
            workdir="/workspace",
        )

        from agentscope.tool._builtin._backend import ExecResult

        with patch.object(
            backend,
            "exec_shell",
            new=AsyncMock(
                return_value=ExecResult(
                    exit_code=1,
                    stdout=b"",
                    stderr=b"No such file or directory",
                ),
            ),
        ):
            with self.assertRaises(FileNotFoundError):
                await backend.read_file("/workspace/missing.txt")


class TestK8sBackendWriteFile(IsolatedAsyncioTestCase):
    """Test K8sBackend.write_file (tar injection)."""

    async def test_write_file_creates_tar(self) -> None:
        """write_file constructs a tar and sends it via WS stdin."""
        from agentscope.workspace._k8s._k8s_backend import K8sBackend

        mock_api_client = MagicMock()
        mock_api_client.configuration = MagicMock()

        backend = K8sBackend(
            api_client=mock_api_client,
            namespace="default",
            pod_name="test-pod",
            container_name="workspace",
            workdir="/workspace",
        )

        from agentscope.tool._builtin._backend import ExecResult

        mock_v1 = MagicMock()
        mock_sock = AsyncMock()
        mock_sock.__aenter__ = AsyncMock(return_value=mock_sock)
        mock_sock.__aexit__ = AsyncMock(return_value=False)
        mock_sock.send_bytes = AsyncMock()
        mock_sock.close = AsyncMock()
        mock_v1.connect_get_namespaced_pod_exec = AsyncMock(
            return_value=mock_sock,
        )

        mock_ws_api = AsyncMock()
        mock_ws_api.__aenter__ = AsyncMock(return_value=mock_ws_api)
        mock_ws_api.__aexit__ = AsyncMock(return_value=False)

        mock_exec = AsyncMock(
            return_value=ExecResult(
                exit_code=0,
                stdout=b"",
                stderr=b"",
            ),
        )

        with (
            patch.object(
                backend,
                "exec_shell",
                new=mock_exec,
            ),
            patch(
                "kubernetes_asyncio.stream.WsApiClient",
                return_value=mock_ws_api,
            ),
            patch(
                "kubernetes_asyncio.client.CoreV1Api",
                return_value=mock_v1,
            ),
        ):
            await backend.write_file(
                "/workspace/out.txt",
                b"test data",
            )

            # Verify mkdir -p was called
            mock_exec.assert_called()

            # Verify tar was sent via stdin, then EOF
            self.assertEqual(mock_sock.send_bytes.call_count, 2)
            sent = mock_sock.send_bytes.call_args_list[0][0][0]
            eof = mock_sock.send_bytes.call_args_list[1][0][0]
            # Channel 0 prefix (stdin)
            self.assertEqual(sent[0], 0)
            # EOF is an empty channel-0 message
            self.assertEqual(eof, bytes([0]))

            # Verify the tar content is valid
            tar_data = sent[1:]
            with tarfile.open(
                fileobj=io.BytesIO(tar_data),
                mode="r",
            ) as tf:
                members = tf.getmembers()
                self.assertEqual(len(members), 1)
                self.assertEqual(members[0].name, "out.txt")
                extracted = tf.extractfile(members[0])
                self.assertIsNotNone(extracted)
                self.assertEqual(extracted.read(), b"test data")


# ── K8sWorkspaceManager tests ────────────────────────────────────


class TestK8sWorkspaceManagerCache(IsolatedAsyncioTestCase):
    """Test K8sWorkspaceManager cache and TTL logic."""

    async def test_create_workspace_caches(self) -> None:
        """create_workspace adds the workspace to the cache."""
        from agentscope.app.workspace_manager import K8sWorkspaceManager

        mgr = K8sWorkspaceManager(ttl=3600.0, sweep_interval=9999.0)

        mock_ws = MagicMock()
        mock_ws.workspace_id = "test-ws-123"
        mock_ws.initialize = AsyncMock()
        mock_ws.close = AsyncMock()

        with patch.object(
            mgr,
            "_build_and_start",
            new=AsyncMock(return_value=mock_ws),
        ):
            ws = await mgr.create_workspace("u1", "a1", "s1")

        self.assertEqual(ws.workspace_id, "test-ws-123")
        self.assertIn("test-ws-123", mgr._cache)

    async def test_get_workspace_cache_hit(self) -> None:
        """get_workspace returns cached workspace on hit."""
        from agentscope.app.workspace_manager import K8sWorkspaceManager

        mgr = K8sWorkspaceManager(ttl=3600.0, sweep_interval=9999.0)

        mock_ws = MagicMock()
        mock_ws.workspace_id = "cached-ws"
        mock_ws.close = AsyncMock()

        import time

        mgr._cache["cached-ws"] = (mock_ws, time.monotonic())

        ws = await mgr.get_workspace("u1", "a1", "s1", "cached-ws")
        self.assertIs(ws, mock_ws)

    async def test_close_removes_from_cache(self) -> None:
        """close() evicts the workspace from cache."""
        from agentscope.app.workspace_manager import K8sWorkspaceManager

        mgr = K8sWorkspaceManager(ttl=3600.0, sweep_interval=9999.0)

        mock_ws = MagicMock()
        mock_ws.workspace_id = "to-close"
        mock_ws.close = AsyncMock()

        import time

        mgr._cache["to-close"] = (mock_ws, time.monotonic())

        await mgr.close("to-close")

        self.assertNotIn("to-close", mgr._cache)
        mock_ws.close.assert_called_once()

    async def test_close_all_clears_cache(self) -> None:
        """close_all() empties the cache."""
        from agentscope.app.workspace_manager import K8sWorkspaceManager

        mgr = K8sWorkspaceManager(ttl=3600.0, sweep_interval=9999.0)

        import time

        for i in range(3):
            mock_ws = MagicMock()
            mock_ws.workspace_id = f"ws-{i}"
            mock_ws.close = AsyncMock()
            mgr._cache[f"ws-{i}"] = (mock_ws, time.monotonic())

        await mgr.close_all()
        self.assertEqual(len(mgr._cache), 0)


# ── constants consistency tests ──────────────────────────────────


class TestConstants(unittest.TestCase):
    """Verify that K8s constants are consistent."""

    def test_default_image(self) -> None:
        """Default image is python:3.11-slim."""
        self.assertEqual(DEFAULT_IMAGE, "python:3.11-slim")

    def test_default_port(self) -> None:
        """Default gateway port is 5600."""
        self.assertEqual(DEFAULT_GATEWAY_PORT, 5600)

    def test_workdir_layout(self) -> None:
        """Workspace layout paths are under POD_WORKDIR."""
        self.assertTrue(POD_DATA_DIR.startswith(POD_WORKDIR))
        self.assertTrue(POD_SKILLS_DIR.startswith(POD_WORKDIR))
        self.assertTrue(POD_SESSIONS_DIR.startswith(POD_WORKDIR))
        self.assertTrue(POD_MCP_FILE.startswith(POD_WORKDIR))

    def test_gateway_layout(self) -> None:
        """Gateway layout paths are under GATEWAY_HOME."""
        self.assertTrue(GATEWAY_SCRIPT.startswith(GATEWAY_HOME))
        self.assertTrue(GATEWAY_VENV_PY.startswith(GATEWAY_HOME))


# ── K8sWorkspace lifecycle tests (mocked K8s API) ────────────────


def _mock_pvc(
    phase: str = "Bound",
    deletion_timestamp: object = None,
) -> MagicMock:
    """Build a mock PVC object with the given phase.

    Args:
        phase: PVC status phase (``Bound``, ``Pending``, ``Lost``).
        deletion_timestamp: When set, simulates a PVC that K8s has
            accepted for deletion but whose finalizers have not yet
            completed.
    """
    pvc = MagicMock()
    pvc.status = MagicMock()
    pvc.status.phase = phase
    pvc.metadata = MagicMock()
    pvc.metadata.deletion_timestamp = deletion_timestamp
    return pvc


def _mock_pod(phase: str = "Running") -> MagicMock:
    """Build a mock Pod object with the given phase."""
    pod = MagicMock()
    pod.status = MagicMock()
    pod.status.phase = phase
    pod.status.pod_ip = "10.0.0.42"
    pod.status.container_statuses = None
    pod.status.conditions = None
    return pod


class _FakeApiException(Exception):
    """Mimics ``kubernetes_asyncio.client.rest.ApiException``."""

    def __init__(self, status: int = 404) -> None:
        self.status = status
        super().__init__(f"ApiException({status})")


class TestEnsurePvcLifecycle(IsolatedAsyncioTestCase):
    """Test ``_ensure_pvc`` branches with mocked K8s API."""

    async def test_pvc_bound_reuse(self) -> None:
        """Existing Bound PVC is reused without recreation."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pvc-bound")
        ws._pod_name = "as-ws-test-pvc-bound"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_persistent_volume_claim = AsyncMock(
            return_value=_mock_pvc("Bound"),
        )

        with patch(
            "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
            "._create_pvc",
            new=AsyncMock(),
        ) as mock_create:
            with patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ):
                await ws._ensure_pvc()

        mock_create.assert_not_called()

    async def test_pvc_404_creates_new(self) -> None:
        """Missing PVC triggers creation."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pvc-new")
        ws._pod_name = "as-ws-test-pvc-new"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=_FakeApiException(404),
        )

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pvc",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pvc()

        mock_create.assert_called_once_with("as-ws-test-pvc-new")

    async def test_pvc_deleting_waits_then_recreates(self) -> None:
        """PVC with deletion_timestamp is waited on, then recreated."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pvc-term")
        ws._pod_name = "as-ws-test-pvc-term"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_persistent_volume_claim = AsyncMock(
            return_value=_mock_pvc(
                "Bound",
                deletion_timestamp="2026-07-02T00:00:00Z",
            ),
        )

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._wait_pvc_deleted",
                new=AsyncMock(),
            ) as mock_wait,
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pvc",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pvc()

        mock_wait.assert_called_once()
        mock_create.assert_called_once()


class TestEnsurePodLifecycle(IsolatedAsyncioTestCase):
    """Test ``_ensure_pod`` branches with mocked K8s API."""

    async def test_pod_running_reuse(self) -> None:
        """Running Pod is reused without recreation."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pod-run")
        ws._pod_name = "as-ws-test-pod-run"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_pod = AsyncMock(
            return_value=_mock_pod("Running"),
        )

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pod",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pod()

        mock_create.assert_not_called()

    async def test_pod_pending_not_rebuilt(self) -> None:
        """Pending Pod is NOT rebuilt — left for _wait_pod_running."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pod-pend")
        ws._pod_name = "as-ws-test-pod-pend"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_pod = AsyncMock(
            return_value=_mock_pod("Pending"),
        )

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pod",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pod()

        mock_create.assert_not_called()
        ws._v1.delete_namespaced_pod.assert_not_called()

    async def test_pod_failed_rebuilds(self) -> None:
        """Failed Pod is deleted and recreated."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pod-fail")
        ws._pod_name = "as-ws-test-pod-fail"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_pod = AsyncMock(
            return_value=_mock_pod("Failed"),
        )
        ws._v1.delete_namespaced_pod = AsyncMock()

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._wait_pod_deleted",
                new=AsyncMock(),
            ),
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pod",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pod()

        ws._v1.delete_namespaced_pod.assert_called_once()
        mock_create.assert_called_once()

    async def test_pod_404_creates_new(self) -> None:
        """Missing Pod triggers creation."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-pod-new")
        ws._pod_name = "as-ws-test-pod-new"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()
        ws._v1.read_namespaced_pod = AsyncMock(
            side_effect=_FakeApiException(404),
        )

        with (
            patch(
                "agentscope.workspace._k8s._k8s_workspace.K8sWorkspace"
                "._create_pod",
                new=AsyncMock(),
            ) as mock_create,
            patch(
                "kubernetes_asyncio.client.rest.ApiException",
                _FakeApiException,
            ),
        ):
            await ws._ensure_pod()

        mock_create.assert_called_once()


class TestWaitPodPendingDetection(IsolatedAsyncioTestCase):
    """Test ``_wait_pod_running`` early failure on Pending conditions."""

    async def test_image_pull_backoff_raises_early(self) -> None:
        """ImagePullBackOff is detected without waiting for timeout."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-ipb")
        ws._pod_name = "as-ws-test-ipb"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()

        pod = _mock_pod("Pending")
        cs = MagicMock()
        cs.state = MagicMock()
        cs.state.waiting = MagicMock()
        cs.state.waiting.reason = "ImagePullBackOff"
        cs.state.waiting.message = "Back-off pulling image"
        pod.status.container_statuses = [cs]
        ws._v1.read_namespaced_pod = AsyncMock(return_value=pod)

        with self.assertRaises(RuntimeError) as ctx:
            await ws._wait_pod_running(timeout=5.0)

        self.assertIn("Back-off pulling image", str(ctx.exception))

    async def test_unschedulable_raises_early(self) -> None:
        """Unschedulable Pod condition is detected early."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-unsched")
        ws._pod_name = "as-ws-test-unsched"
        ws._namespace = "agentscope"
        ws._v1 = AsyncMock()

        pod = _mock_pod("Pending")
        cond = MagicMock()
        cond.type = "PodScheduled"
        cond.status = "False"
        cond.reason = "Unschedulable"
        cond.message = "0/3 nodes are available"
        pod.status.conditions = [cond]
        ws._v1.read_namespaced_pod = AsyncMock(return_value=pod)

        with self.assertRaises(RuntimeError) as ctx:
            await ws._wait_pod_running(timeout=5.0)

        self.assertIn("unschedulable", str(ctx.exception).lower())


class TestDeletePvcOnClose(IsolatedAsyncioTestCase):
    """Test the _delete_pvc_on_close constructor parameter."""

    async def test_default_is_false(self) -> None:
        """_delete_pvc_on_close defaults to False."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(workspace_id="test-default")
        self.assertFalse(ws._delete_pvc_on_close)

    async def test_constructor_sets_true(self) -> None:
        """delete_pvc_on_close=True is stored as private attribute."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        ws = K8sWorkspace(
            workspace_id="test-ctor",
            delete_pvc_on_close=True,
        )
        self.assertTrue(ws._delete_pvc_on_close)


class TestK8sWorkspaceInheritance(unittest.TestCase):
    """Verify K8sWorkspace correctly inherits SandboxedWorkspaceBase."""

    def test_inherits_sandboxed_base(self) -> None:
        """K8sWorkspace is a subclass of SandboxedWorkspaceBase."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace
        from agentscope.workspace._sandboxed_base import (
            SandboxedWorkspaceBase,
        )

        self.assertTrue(issubclass(K8sWorkspace, SandboxedWorkspaceBase))

    def test_class_attributes_set(self) -> None:
        """K8sWorkspace sets all required class attributes."""
        from agentscope.workspace._k8s._k8s_workspace import K8sWorkspace

        for attr in (
            "_glob_helper_path",
            "_gateway_home",
            "_gateway_config",
            "_gateway_log",
            "_gateway_script",
            "_gateway_python",
        ):
            self.assertTrue(
                hasattr(K8sWorkspace, attr),
                f"Missing class attribute {attr}",
            )
            val = getattr(K8sWorkspace, attr)
            self.assertIsInstance(val, str)
            self.assertTrue(len(val) > 0)


if __name__ == "__main__":
    unittest.main()
