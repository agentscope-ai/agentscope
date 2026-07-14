# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerWorkspace`.

Validates the workspace lifecycle (initialize / close), configuration,
and integration with :class:`AppleContainerBackend`. All subprocess
calls are mocked — no real ``container`` CLI is required.

For live integration tests (macOS 26+ with ``container`` installed),
run with the ``APPLE_CONTAINER_LIVE`` environment variable set.
"""

import asyncio
import json
import os
import sys
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from unittest import skipUnless

from agentscope.workspace._applecontainer._applecontainer_backend import (
    AppleContainerBackend,
)
from agentscope.workspace._applecontainer._constants import (
    CONTAINER_WORKDIR,
    DEFAULT_BASE_IMAGE,
)
from agentscope.workspace._applecontainer._applecontainer_workspace import (
    AppleContainerWorkspace,
)

# ── availability check ──────────────────────────────────────────────

_APPLE_CONTAINER_LIVE = os.getenv("APPLE_CONTAINER_LIVE", "")


# ── mock helpers ────────────────────────────────────────────────────


def _mock_process(
    exit_code: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Build a mock subprocess."""
    proc = MagicMock()
    proc.returncode = exit_code
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _mock_version_output() -> MagicMock:
    """Return a valid ``container system version --format json`` output."""
    return _mock_process(
        exit_code=0,
        stdout=json.dumps(
            [
                {
                    "appName": "container",
                    "version": "1.0.0",
                    "buildType": "release",
                    "commit": "abc123",
                },
            ],
        ).encode(),
    )


def _mock_image_list_output(  # type: ignore[no-untyped-def]
    images=None,
):
    """Return a ``container image list --format json`` output."""
    if images is None:
        images = []
    return _mock_process(
        exit_code=0,
        stdout=json.dumps(images).encode(),
    )


def _mock_container_list_output(  # type: ignore[no-untyped-def]
    containers=None,
):
    """Return a ``container list --format json`` output."""
    if containers is None:
        containers = []
    return _mock_process(
        exit_code=0,
        stdout=json.dumps(containers).encode(),
    )


def _mock_inspect_output(status: str = "running") -> MagicMock:
    """Return a ``container inspect`` output."""
    return _mock_process(
        exit_code=0,
        stdout=json.dumps({"status": status, "id": "abc123"}).encode(),
    )


# ── tests (mocked) ──────────────────────────────────────────────────


@skipUnless(sys.platform == "darwin", "Apple Container tests require macOS")
class TestAppleContainerWorkspaceConstruct(IsolatedAsyncioTestCase):
    """Constructor and config tests — no subprocess needed."""

    def test_default_values(self) -> None:
        """Constructor picks up all the default constants."""
        ws = AppleContainerWorkspace()
        self.assertEqual(ws.workdir, CONTAINER_WORKDIR)
        self.assertEqual(ws.base_image, DEFAULT_BASE_IMAGE)
        self.assertEqual(ws.cpus, 2)
        self.assertEqual(ws.memory, "2G")
        self.assertFalse(ws.is_alive)

    def test_custom_values(self) -> None:
        """All config knobs are forwarded."""
        ws = AppleContainerWorkspace(
            workspace_id="my-ws",
            base_image="ubuntu:latest",
            gateway_port=9999,
            cpus=4,
            memory="8G",
            env={"FOO": "bar"},
            extra_pip=["requests"],
        )
        self.assertEqual(ws.workspace_id, "my-ws")
        self.assertEqual(ws.base_image, "ubuntu:latest")
        self.assertEqual(ws.gateway_port, 9999)
        self.assertEqual(ws.cpus, 4)
        self.assertEqual(ws.memory, "8G")
        self.assertEqual(ws.env, {"FOO": "bar"})
        self.assertEqual(ws.extra_pip, ["requests"])

    def test_instructions_substitution(self):
        """System prompt contains the container workdir."""
        ws = AppleContainerWorkspace()
        # get_instructions is sync for this template — call it directly.
        loop = asyncio.get_event_loop()
        text = loop.run_until_complete(ws.get_instructions())
        self.assertIn(CONTAINER_WORKDIR, text)


@skipUnless(sys.platform == "darwin", "Apple Container tests require macOS")
class TestAppleContainerWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Lifecycle tests with mocked subprocess."""

    def setUp(self):
        """Create a workspace with mocked subprocess."""
        self.ws = AppleContainerWorkspace(workspace_id="test-lifecycle")

    def _setup_mocks(self, mock_create):
        """Configure the mock subprocess call sequence.

        Handles:
        - container system version (CLI check)
        - container image list (check existing images)
        - container image pull (pull if needed)
        - container list --all (check existing container)
        - container run -d (create & start)
        - container inspect (check status)
        - container exec (from backend — returns success)
        - container stop / rm (teardown)
        """

        def _side_effect(*args: object, **kwargs: object) -> object:
            _ = kwargs  # unused — needed for mock signature compatibility
            cmd_args = args
            if len(cmd_args) >= 3 and cmd_args[0] == "container":
                subcmd = cmd_args[1]

                if subcmd == "system" and len(cmd_args) >= 4:
                    if cmd_args[2] == "version":
                        return _mock_version_output()

                if subcmd == "image" and len(cmd_args) >= 4:
                    if cmd_args[2] == "list":
                        return _mock_image_list_output()
                if subcmd == "image" and len(cmd_args) >= 3:
                    if cmd_args[2] == "pull":
                        return _mock_process(exit_code=0)

                if subcmd == "list":
                    return _mock_container_list_output()

                if subcmd == "run":
                    return _mock_process(
                        exit_code=0,
                        stdout=b"container-abc123\n",
                    )

                if subcmd == "inspect":
                    return _mock_inspect_output("running")

                if subcmd in ("stop", "rm"):
                    return _mock_process(exit_code=0)

                if subcmd == "exec":
                    return _mock_process(
                        exit_code=0,
                        stdout=b"OK\n",
                    )

                if subcmd == "cp":
                    return _mock_process(exit_code=0)

            # Default: success
            return _mock_process(exit_code=0)

        mock_create.side_effect = _side_effect

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    @patch.object(
        AppleContainerWorkspace,
        "_setup_mcp_gateway",
        new_callable=AsyncMock,
    )
    async def test_initialize_creates_container(
        self,
        mock_gateway,
        mock_create,
    ):
        """``initialize`` provisions a container and binds the backend."""
        self._setup_mocks(mock_create)

        await self.ws.initialize()
        self.assertTrue(self.ws.is_alive)
        self.assertIsNotNone(self.ws._backend)
        self.assertIsInstance(self.ws._backend, AppleContainerBackend)
        mock_gateway.assert_called_once()

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    @patch.object(
        AppleContainerWorkspace,
        "_setup_mcp_gateway",
        new_callable=AsyncMock,
    )
    async def test_close_stops_and_removes_container(
        self,
        mock_gateway,  # noqa: ARG002
        mock_create,
    ):
        """``close`` calls ``container stop`` then ``container rm -f``."""
        _ = mock_gateway  # passed by patch.object, unused in this test
        self._setup_mocks(mock_create)
        await self.ws.initialize()

        # Reset mock tracking to check close calls.
        mock_create.reset_mock()
        mock_create.side_effect = None

        stop_proc = _mock_process(exit_code=0)
        rm_proc = _mock_process(exit_code=0)
        mock_create.side_effect = [stop_proc, rm_proc]

        await self.ws.close()
        self.assertFalse(self.ws.is_alive)
        self.assertIsNone(self.ws._backend)

        # Verify stop was called.
        calls = mock_create.call_args_list
        cmd_args = [c[0] for c in calls]
        self.assertTrue(
            any("stop" in a for a in cmd_args),
            f"Expected 'container stop', got: {cmd_args}",
        )

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    @patch.object(
        AppleContainerWorkspace,
        "_setup_mcp_gateway",
        new_callable=AsyncMock,
    )
    async def test_initialize_idempotent(
        self,
        mock_gateway,
        mock_create,
    ):
        """Calling ``initialize`` twice is a no-op on the second call."""
        self._setup_mocks(mock_create)
        await self.ws.initialize()

        call_count_before = mock_create.call_count
        # Second initialize should be a no-op.
        await self.ws.initialize()
        self.assertEqual(
            mock_create.call_count,
            call_count_before,
            "Second initialize should be a no-op",
        )
        # Gateway setup should only be called once.
        self.assertEqual(mock_gateway.call_count, 1)

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_cli_not_available_raises(self, mock_create):
        """When ``container`` CLI is not installed, RuntimeError is raised."""
        mock_create.return_value = _mock_process(
            exit_code=1,
            stderr=b"container: command not found",
        )

        with self.assertRaises(RuntimeError) as ctx:
            await self.ws.initialize()
        self.assertIn("CLI is not available", str(ctx.exception))

    def test_bootstrap_commands(self):
        """``_bootstrap_commands`` returns expected commands with backend."""
        # Set up a fake backend so _gateway_venv works.
        self.ws._backend = AppleContainerBackend(
            container_id="test",
            workdir="/workspace",
        )
        cmds = self.ws._bootstrap_commands()
        self.assertIsInstance(cmds, list)
        self.assertGreater(len(cmds), 0)
        # First command should install system deps.
        self.assertIn("apt-get", cmds[0])
        # Should include uv install.
        self.assertTrue(
            any("curl" in c for c in cmds),
            "Bootstrap should include curl for uv install",
        )
        # Should include agentscope install.
        self.assertTrue(
            any("agentscope" in c for c in cmds),
            "Bootstrap should install agentscope",
        )


# ── live tests (macOS 26+ only) ─────────────────────────────────────


@skipUnless(
    _APPLE_CONTAINER_LIVE,
    "APPLE_CONTAINER_LIVE environment variable is not set",
)
@skipUnless(sys.platform == "darwin", "Apple Container tests require macOS")
class TestAppleContainerWorkspaceLive(IsolatedAsyncioTestCase):
    """Live integration tests requiring macOS 26+ and ``container`` CLI.

    Set ``APPLE_CONTAINER_LIVE=1`` to run these tests.
    """

    async def test_full_lifecycle(self):
        """Create, use, and close a real Apple Container workspace."""
        ws = AppleContainerWorkspace()
        async with ws:
            self.assertTrue(ws.is_alive)
            backend = ws.get_backend()
            self.assertIsInstance(backend, AppleContainerBackend)

            # Run a simple command.
            result = await backend.exec_shell(["echo", "hello from test"])
            self.assertTrue(result.ok())
            self.assertEqual(
                result.stdout.decode().strip(),
                "hello from test",
            )

            # Write and read a file.
            test_path = f"{CONTAINER_WORKDIR}/live_test.txt"
            await backend.write_file(test_path, b"live test data")
            data = await backend.read_file(test_path)
            self.assertEqual(data, b"live test data")

        self.assertFalse(ws.is_alive)
