# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`OpenSandboxWorkspaceManager`."""

import asyncio
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    created: list["_FakeWorkspace"] = []
    sandboxes: list[MagicMock] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.closed = False
        self._sandbox = MagicMock()
        self._sandbox.kill = AsyncMock()
        _FakeWorkspace.sandboxes.append(self._sandbox)
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Yield once so builds can interleave."""
        await asyncio.sleep(0)

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


class TestOpenSandboxWorkspaceManager(IsolatedAsyncioTestCase):
    """Dispose of half-initialized workspaces."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        _FakeWorkspace.sandboxes.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_failed_initialize_kills_and_closes(self) -> None:
        """A failed initialize kills the sandbox and closes the ws."""
        manager = OpenSandboxWorkspaceManager()
        with patch.object(
            _FakeWorkspace,
            "initialize",
            AsyncMock(side_effect=RuntimeError("gateway failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "gateway failed"):
                await manager.get_workspace(
                    "u1",
                    "a1",
                    "s1",
                    workspace_id="ws-id",
                )

        ws = _FakeWorkspace.created[0]
        _FakeWorkspace.sandboxes[0].kill.assert_awaited_once()
        self.assertIsNone(ws._sandbox)
        self.assertTrue(ws.closed)

    async def test_successful_initialize_leaves_the_sandbox(self) -> None:
        """A healthy initialize neither kills nor closes."""
        manager = OpenSandboxWorkspaceManager()
        ws = await manager.get_workspace(
            "u1",
            "a1",
            "s1",
            workspace_id="ws-id",
        )
        ws._sandbox.kill.assert_not_called()
        self.assertFalse(ws.closed)
