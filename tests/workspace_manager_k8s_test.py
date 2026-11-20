# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`K8sWorkspaceManager`."""

import asyncio
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import K8sWorkspaceManager


class _FakeWorkspace:
    """Workspace double used by manager tests.

    The manager module's ``K8sWorkspace`` is replaced, so the lifecycle
    can be exercised without a cluster or the Kubernetes SDK.
    """

    created: list["_FakeWorkspace"] = []
    initialize_error: Exception | None = None
    close_error: Exception | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        self.close_calls = 0
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized, or raise the configured error."""
        await asyncio.sleep(0)
        if _FakeWorkspace.initialize_error is not None:
            raise _FakeWorkspace.initialize_error
        self.initialized = True

    async def close(self) -> None:
        """Record the close attempt, or raise the configured error."""
        self.close_calls += 1
        if _FakeWorkspace.close_error is not None:
            raise _FakeWorkspace.close_error
        self.closed = True


class TestK8sWorkspaceManagerLifecycle(IsolatedAsyncioTestCase):
    """A failed ``initialize`` must dispose of the half-started Pod."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        _FakeWorkspace.initialize_error = None
        _FakeWorkspace.close_error = None
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_k8s_workspace_manager.K8sWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_failed_initialize_closes_the_pod(self) -> None:
        """A workspace whose ``initialize`` fails is closed exactly once."""
        _FakeWorkspace.initialize_error = RuntimeError("pod failed")
        manager = K8sWorkspaceManager()

        with self.assertRaisesRegex(RuntimeError, "pod failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 1)
        workspace = _FakeWorkspace.created[0]
        self.assertEqual(workspace.close_calls, 1)
        self.assertTrue(workspace.closed)
        self.assertEqual(manager._cache, {})

    async def test_dispose_error_does_not_mask_initialize_error(self) -> None:
        """Cleanup failures are swallowed; the original error wins."""
        _FakeWorkspace.initialize_error = ValueError("gateway failed")
        _FakeWorkspace.close_error = RuntimeError("close failed")
        manager = K8sWorkspaceManager()

        with self.assertRaisesRegex(ValueError, "gateway failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(manager._cache, {})

    async def test_retry_after_failure_recreates_the_workspace(self) -> None:
        """Nothing is cached on failure, so the next call builds anew."""
        _FakeWorkspace.initialize_error = RuntimeError("pod failed")
        manager = K8sWorkspaceManager()
        with self.assertRaisesRegex(RuntimeError, "pod failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        _FakeWorkspace.initialize_error = None
        workspace = await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 2)
        self.assertIs(manager._cache["ws-id"][0], workspace)
        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(_FakeWorkspace.created[1].close_calls, 0)


if __name__ == "__main__":
    unittest.main()
