# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`K8sWorkspaceManager`."""

import asyncio
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import K8sWorkspaceManager


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.closed = False
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Yield once so builds can interleave."""
        await asyncio.sleep(0)

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


class TestK8sWorkspaceManager(IsolatedAsyncioTestCase):
    """Dispose of half-initialized workspaces."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_k8s_workspace_manager.K8sWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_failed_initialize_closes_the_workspace(self) -> None:
        """A workspace whose ``initialize`` fails is closed, not leaked."""
        manager = K8sWorkspaceManager()
        with patch.object(
            _FakeWorkspace,
            "initialize",
            AsyncMock(side_effect=RuntimeError("gateway failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "gateway failed"):
                await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertListEqual(
            [ws.closed for ws in _FakeWorkspace.created],
            [True],
        )

    async def test_successful_initialize_stays_open(self) -> None:
        """A healthy initialize does not close the workspace."""
        manager = K8sWorkspaceManager()
        await manager.get_workspace("u1", "a1", "s1", "ws-id")
        self.assertListEqual(
            [ws.closed for ws in _FakeWorkspace.created],
            [False],
        )
