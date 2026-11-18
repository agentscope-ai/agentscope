# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`OpenSandboxWorkspaceManager`."""

import asyncio
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import (
    OpenSandboxWorkspaceManager,
)
from agentscope.workspace import OpenSandboxWorkspace


class _FakeSandbox:
    """OpenSandbox sandbox double recording kill / pause / local close."""

    def __init__(self) -> None:
        self.killed = False
        self.paused = False
        self.local_closed = False
        self.kill_error: Exception | None = None

    async def kill(self) -> None:
        """Record a permanent delete, or raise the configured error."""
        if self.kill_error is not None:
            raise self.kill_error
        self.killed = True

    async def pause(self) -> None:
        """Record a snapshot-preserving pause."""
        self.paused = True

    async def close(self) -> None:
        """Record local resource release."""
        self.local_closed = True


class _FakeWorkspace:
    """Workspace double whose ``close`` pauses, as OpenSandbox's does."""

    created: list["_FakeWorkspace"] = []
    initialize_error: Exception | None = None
    close_error: Exception | None = None
    sandbox_kill_error: Exception | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        self.close_calls = 0
        self._sandbox: _FakeSandbox | None = _FakeSandbox()
        self._sandbox_created = True
        self._sandbox.kill_error = _FakeWorkspace.sandbox_kill_error
        # Stable handle: disposal clears ``_sandbox`` on success.
        self.sandbox = self._sandbox
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized, or raise the configured error."""
        await asyncio.sleep(0)
        if _FakeWorkspace.initialize_error is not None:
            raise _FakeWorkspace.initialize_error
        self.initialized = True

    async def close(self) -> None:
        """Pause and release local resources, as the real close does."""
        self.close_calls += 1
        if _FakeWorkspace.close_error is not None:
            raise _FakeWorkspace.close_error
        if self._sandbox is not None:
            await self._sandbox.pause()
            await self._sandbox.close()
            self._sandbox = None
        self.closed = True


class TestOpenSandboxGatewayFailure(IsolatedAsyncioTestCase):
    """Exercise the real workspace's create and reattach paths."""

    async def test_gateway_timeout_preserves_restored_sandbox(self) -> None:
        """Pause a restored sandbox and kill a newly created one."""
        for existing in (True, False):
            with self.subTest(existing=existing):
                manager = OpenSandboxWorkspaceManager()
                sandbox = _FakeSandbox()
                with (
                    patch.multiple(
                        OpenSandboxWorkspace,
                        _find_existing_sandbox=AsyncMock(
                            return_value=sandbox if existing else None,
                        ),
                        _attach_existing_sandbox=AsyncMock(
                            return_value=sandbox,
                        ),
                        _create_sandbox=AsyncMock(return_value=sandbox),
                        _restore_mcp_specs=AsyncMock(return_value={}),
                        _ensure_workspace_layout=AsyncMock(),
                        _setup_mcp_gateway=AsyncMock(side_effect=TimeoutError),
                    ),
                    self.assertRaises(TimeoutError),
                ):
                    await manager.get_workspace("u", "a", "s", "ws")
                self.assertEqual(manager._cache, {})
                self.assertEqual(
                    (sandbox.killed, sandbox.paused, sandbox.local_closed),
                    (not existing, existing, True),
                )


class TestOpenSandboxWorkspaceManagerLifecycle(IsolatedAsyncioTestCase):
    """A failed ``initialize`` must kill the half-started sandbox."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        _FakeWorkspace.initialize_error = None
        _FakeWorkspace.close_error = None
        _FakeWorkspace.sandbox_kill_error = None
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_failed_initialize_kills_the_sandbox(self) -> None:
        """Kill (not pause) the sandbox and release local resources."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        manager = OpenSandboxWorkspaceManager()

        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 1)
        workspace = _FakeWorkspace.created[0]
        self.assertEqual(workspace.close_calls, 1)
        self.assertTrue(workspace.sandbox.killed)
        self.assertFalse(workspace.sandbox.paused)
        self.assertTrue(workspace.sandbox.local_closed)
        self.assertTrue(workspace.closed)
        self.assertEqual(manager._cache, {})

    async def test_failed_kill_falls_back_to_pause(self) -> None:
        """If the kill fails, the ordinary close path still runs."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        _FakeWorkspace.sandbox_kill_error = RuntimeError("kill failed")
        manager = OpenSandboxWorkspaceManager()

        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        sandbox = _FakeWorkspace.created[0].sandbox
        self.assertFalse(sandbox.killed)
        self.assertTrue(sandbox.paused)
        self.assertTrue(sandbox.local_closed)

    async def test_dispose_error_does_not_mask_initialize_error(self) -> None:
        """Cleanup failures are swallowed; the original error wins."""
        _FakeWorkspace.initialize_error = ValueError("gateway failed")
        _FakeWorkspace.close_error = RuntimeError("close failed")
        _FakeWorkspace.sandbox_kill_error = RuntimeError("kill failed")
        manager = OpenSandboxWorkspaceManager()

        with self.assertRaisesRegex(ValueError, "gateway failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(manager._cache, {})

    async def test_retry_after_failure_recreates_the_sandbox(self) -> None:
        """Nothing is cached on failure, so the next call builds anew."""
        _FakeWorkspace.initialize_error = RuntimeError("sandbox failed")
        manager = OpenSandboxWorkspaceManager()
        with self.assertRaisesRegex(RuntimeError, "sandbox failed"):
            await manager.get_workspace("u1", "a1", "s1", "ws-id")

        _FakeWorkspace.initialize_error = None
        workspace = await manager.get_workspace("u1", "a1", "s1", "ws-id")

        self.assertEqual(len(_FakeWorkspace.created), 2)
        self.assertIs(manager._cache["ws-id"][0], workspace)
        self.assertEqual(_FakeWorkspace.created[0].close_calls, 1)
        self.assertEqual(_FakeWorkspace.created[1].close_calls, 0)


if __name__ == "__main__":
    unittest.main()
