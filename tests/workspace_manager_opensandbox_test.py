# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenSandbox workspace lease and cache recovery."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from opensandbox.exceptions import SandboxApiException

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
from agentscope.workspace import OpenSandboxWorkspace


class _FakeSandbox:
    """OpenSandbox SDK double for lifecycle calls."""

    def __init__(self, state: str = "Running") -> None:
        """Initialize a running sandbox double.

        Args:
            state (`str`, defaults to `"Running"`):
                Lifecycle state returned by :meth:`get_info`.
        """
        self.id = "sandbox-1"
        self.state = state
        self.info_error: Exception | None = None
        self.renew_error: Exception | None = None
        self.renewed: list[timedelta] = []
        self.closed = False

    async def get_info(self) -> SimpleNamespace:
        """Return the configured lifecycle state."""
        if self.info_error is not None:
            raise self.info_error
        return SimpleNamespace(status=SimpleNamespace(state=self.state))

    async def renew(self, timeout: timedelta) -> None:
        """Record a lease renewal.

        Args:
            timeout (`timedelta`):
                Requested new lease duration.
        """
        if self.renew_error is not None:
            raise self.renew_error
        self.renewed.append(timeout)

    async def close(self) -> None:
        """Record local SDK transport cleanup."""
        self.closed = True


class TestOpenSandboxWorkspaceLease(IsolatedAsyncioTestCase):
    """Workspace-level renewal and lifecycle checks."""

    async def test_running_sandbox_is_renewed(self) -> None:
        """A running cached sandbox receives a fresh finite lease."""
        workspace = OpenSandboxWorkspace(
            workspace_id="wid",
            timeout_seconds=90,
        )
        sandbox = _FakeSandbox()
        workspace._sandbox = sandbox

        state = await workspace._refresh_remote_lifecycle()

        self.assertEqual(
            {
                "state": state,
                "renewed": sandbox.renewed,
                "lease_lost": workspace._lease_lost,
            },
            {
                "state": "running",
                "renewed": [timedelta(seconds=90)],
                "lease_lost": False,
            },
        )

    async def test_paused_sandbox_requests_reattach(self) -> None:
        """A paused sandbox is not treated as missing or renewed."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        sandbox = _FakeSandbox(state="Paused")
        workspace._sandbox = sandbox

        state = await workspace._refresh_remote_lifecycle()

        self.assertEqual(
            {"state": state, "renewed": sandbox.renewed},
            {"state": "reattach", "renewed": []},
        )

    async def test_missing_sandbox_marks_lease_lost(self) -> None:
        """A lifecycle 404 makes the cached handle replaceable."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        sandbox = _FakeSandbox()
        sandbox.info_error = SandboxApiException(status_code=404)
        workspace._sandbox = sandbox

        state = await workspace._refresh_remote_lifecycle()

        self.assertEqual(
            {"state": state, "lease_lost": workspace._lease_lost},
            {"state": "missing", "lease_lost": True},
        )

    async def test_transient_control_plane_error_is_not_missing(self) -> None:
        """A non-404 API failure propagates without poisoning the lease."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        sandbox = _FakeSandbox()
        sandbox.info_error = SandboxApiException(status_code=503)
        workspace._sandbox = sandbox

        with self.assertRaises(SandboxApiException):
            await workspace._refresh_remote_lifecycle()

        self.assertFalse(workspace._lease_lost)

    async def test_discard_stops_renewal_and_closes_local_transport(
        self,
    ) -> None:
        """Recovery drops stale local resources without pausing the remote."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        sandbox = _FakeSandbox()
        workspace._sandbox = sandbox
        workspace.is_alive = True
        workspace._start_renewal()
        renew_task = workspace._renew_task

        await workspace._discard_local_connection()

        self.assertEqual(
            {
                "sandbox_closed": sandbox.closed,
                "sandbox": workspace._sandbox,
                "backend": workspace._backend,
                "is_alive": workspace.is_alive,
                "renew_task": workspace._renew_task,
                "task_done": renew_task.done(),
            },
            {
                "sandbox_closed": True,
                "sandbox": None,
                "backend": None,
                "is_alive": False,
                "renew_task": None,
                "task_done": True,
            },
        )

    async def test_provision_starts_renewal_before_bootstrap(self) -> None:
        """The lease loop starts as soon as the backend is provisioned."""
        workspace = OpenSandboxWorkspace(
            workspace_id="wid",
            timeout_seconds=90,
        )
        sandbox = _FakeSandbox()
        workspace._find_existing_sandbox = AsyncMock(return_value=None)
        workspace._create_sandbox = AsyncMock(return_value=sandbox)
        workspace._wait_until_running = AsyncMock()

        await workspace._provision_backend()
        renew_task = workspace._renew_task
        await workspace._stop_renewal()

        self.assertEqual(
            {
                "renewed": sandbox.renewed,
                "task_created": renew_task is not None,
                "task_done": renew_task.done(),
            },
            {
                "renewed": [timedelta(seconds=90)],
                "task_created": True,
                "task_done": True,
            },
        )

    async def test_initialize_failure_closes_partial_workspace(self) -> None:
        """A bootstrap failure cannot leave a renewal task behind."""
        workspace = OpenSandboxWorkspace(workspace_id="wid")
        workspace.close = AsyncMock()

        with patch(
            "agentscope.workspace._sandboxed_base."
            "SandboxedWorkspaceBase.initialize",
            AsyncMock(side_effect=RuntimeError("bootstrap failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                await workspace.initialize()

        workspace.close.assert_awaited_once()


class _FakeWorkspace:
    """Workspace double used by manager recovery tests."""

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        """Record forwarded manager configuration.

        Args:
            **kwargs (`object`):
                Workspace constructor arguments.
        """
        self.kwargs = kwargs
        self.workspace_id = str(kwargs["workspace_id"])
        self.sandbox_id = f"sandbox-{len(self.created) + 1}"
        self.lifecycle = "running"
        self.refresh_error: Exception | None = None
        self.initialized = False
        self.discarded = False
        self.closed = False
        self.refresh_count = 0
        self.created.append(self)

    async def initialize(self) -> None:
        """Mark full workspace initialization."""
        await asyncio.sleep(0)
        self.initialized = True

    async def _refresh_remote_lifecycle(self) -> str:
        """Return the configured lifecycle result."""
        await asyncio.sleep(0)
        self.refresh_count += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.lifecycle

    async def _discard_local_connection(self) -> None:
        """Record recovery cleanup without remote pause."""
        self.discarded = True

    async def close(self) -> None:
        """Record ordinary manager eviction."""
        self.closed = True


class TestOpenSandboxWorkspaceManagerRecovery(IsolatedAsyncioTestCase):
    """Cache reuse and automatic replacement behavior."""

    async def asyncSetUp(self) -> None:
        """Patch the concrete workspace built by the manager."""
        _FakeWorkspace.created.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo the workspace patch."""
        self.workspace_patch.stop()

    async def test_running_cache_entry_is_reused_and_refreshed(self) -> None:
        """A live cache hit keeps object identity and renews its lease."""
        manager = OpenSandboxWorkspaceManager()

        first = await manager.get_workspace("u", "a", "s1", "wid")
        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertEqual(
            {
                "same": first is second,
                "created": len(_FakeWorkspace.created),
                "refresh_count": first.refresh_count,
            },
            {"same": True, "created": 1, "refresh_count": 1},
        )

    async def test_missing_cache_entry_is_recreated_with_warning(self) -> None:
        """A destroyed sandbox is replaced under the same workspace id."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        first.lifecycle = "missing"

        with patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.logger.warning",
        ) as warning:
            second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertEqual(
            {
                "replaced": first is not second,
                "discarded": first.discarded,
                "created": len(_FakeWorkspace.created),
                "workspace_id": second.workspace_id,
            },
            {
                "replaced": True,
                "discarded": True,
                "created": 2,
                "workspace_id": "wid",
            },
        )
        warning.assert_called_once()

    async def test_paused_cache_entry_is_reattached(self) -> None:
        """A paused sandbox follows normal metadata-based initialization."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s1", "wid")
        first.lifecycle = "reattach"

        second = await manager.get_workspace("u", "a", "s2", "wid")

        self.assertEqual(
            {
                "replaced": first is not second,
                "discarded": first.discarded,
                "created": len(_FakeWorkspace.created),
                "workspace_id": second.workspace_id,
            },
            {
                "replaced": True,
                "discarded": True,
                "created": 2,
                "workspace_id": "wid",
            },
        )

    async def test_transient_error_keeps_cached_workspace(self) -> None:
        """A control-plane outage does not trigger duplicate creation."""
        manager = OpenSandboxWorkspaceManager()
        workspace = await manager.get_workspace("u", "a", "s1", "wid")
        workspace.refresh_error = RuntimeError("control plane unavailable")

        with self.assertRaisesRegex(RuntimeError, "control plane unavailable"):
            await manager.get_workspace("u", "a", "s2", "wid")

        self.assertEqual(
            {
                "cached": manager._cache["wid"][0] is workspace,
                "discarded": workspace.discarded,
                "created": len(_FakeWorkspace.created),
            },
            {"cached": True, "discarded": False, "created": 1},
        )

    async def test_concurrent_expiration_creates_one_replacement(self) -> None:
        """Concurrent cache hits share a single recovery build."""
        manager = OpenSandboxWorkspaceManager()
        first = await manager.get_workspace("u", "a", "s0", "wid")
        first.lifecycle = "missing"

        results = await asyncio.gather(
            *(
                manager.get_workspace("u", "a", f"s{i}", "wid")
                for i in range(8)
            ),
        )

        self.assertEqual(
            {
                "created": len(_FakeWorkspace.created),
                "discarded": first.discarded,
                "one_replacement": all(item is results[0] for item in results),
                "cached_replacement": manager._cache["wid"][0] is results[0],
            },
            {
                "created": 2,
                "discarded": True,
                "one_replacement": True,
                "cached_replacement": True,
            },
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
