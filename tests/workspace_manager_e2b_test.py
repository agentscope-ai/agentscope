# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`E2BWorkspaceManager` pre-warming."""

import asyncio
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    E2BWorkspaceManager,
    IsolationPolicy,
    PrewarmConfig,
)


class _FakeSandbox:
    """E2B sandbox double recording pause / kill."""

    def __init__(self) -> None:
        """Start alive and unpaused."""
        self.killed = False
        self.paused = False

    async def kill(self) -> None:
        """Record a permanent delete."""
        self.killed = True

    async def pause(self) -> None:
        """Record a snapshot-preserving pause."""
        self.paused = True


class _FakeWorkspace:
    """Workspace double whose ``close`` pauses, as E2B's does."""

    created: list["_FakeWorkspace"] = []
    fail_initialize = False

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self._sandbox = _FakeSandbox()
        # Stable handle: the manager clears ``_sandbox`` once killed.
        self.sandbox = self._sandbox
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Yield once so builds can interleave."""
        await asyncio.sleep(0)
        if _FakeWorkspace.fail_initialize:
            raise RuntimeError("gateway bootstrap failed")

    async def close(self) -> None:
        """Pause the sandbox — never kill it."""
        if self._sandbox is not None:
            await self._sandbox.pause()


class TestE2BWorkspaceManagerPrewarm(IsolatedAsyncioTestCase):
    """Hand-off from the buffer and disposal of unclaimed sandboxes."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        _FakeWorkspace.fail_initialize = False
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_e2b_workspace_manager.E2BWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_prewarmed_sandbox_is_handed_over_without_rebuild(
        self,
    ) -> None:
        """The buffered sandbox's id becomes the binding, and
        ``get_workspace`` then answers from the cache."""
        manager = E2BWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
            prewarm=PrewarmConfig(size=1),
        )
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        prewarmed = _FakeWorkspace.created[0]

        workspace_id = await manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )
        ws = await manager.get_workspace("u1", "a1", "s1", workspace_id)
        await asyncio.sleep(0.05)

        self.assertEqual(workspace_id, prewarmed.workspace_id)
        self.assertIs(ws, prewarmed)
        self.assertFalse(prewarmed.sandbox.killed)
        # One replacement build, nothing built for the request itself.
        self.assertEqual(len(_FakeWorkspace.created), 2)

    async def test_unclaimed_sandbox_is_killed_not_paused(self) -> None:
        """Shutdown deletes buffered sandboxes outright — a paused one
        is stranded, since its id was never persisted."""
        manager = E2BWorkspaceManager(prewarm=PrewarmConfig(size=2))
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        self.assertEqual(len(_FakeWorkspace.created), 2)

        await manager._stop_prewarm()

        self.assertListEqual(
            [ws.sandbox.killed for ws in _FakeWorkspace.created],
            [True, True],
        )

    async def test_claimed_sandbox_is_only_paused_on_close(self) -> None:
        """A sandbox handed to a session keeps the reattachable path."""
        manager = E2BWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
            prewarm=PrewarmConfig(size=1),
        )
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        claimed = _FakeWorkspace.created[0]
        workspace_id = await manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )

        await manager.close(workspace_id)

        self.assertFalse(claimed.sandbox.killed)
        self.assertTrue(claimed.sandbox.paused)

    async def test_a_kill_is_not_followed_by_a_pause(self) -> None:
        """Pausing a sandbox that was just killed is a wasted remote
        call that logs a failure on every shutdown."""
        manager = E2BWorkspaceManager(prewarm=PrewarmConfig(size=1))
        manager._start_prewarm()
        await asyncio.sleep(0.05)

        await manager._stop_prewarm()

        sandbox = _FakeWorkspace.created[0].sandbox
        self.assertListEqual([sandbox.killed, sandbox.paused], [True, False])

    async def test_a_half_built_sandbox_is_killed(self) -> None:
        """``initialize`` can fail after the remote sandbox exists, and
        nothing else holds it."""
        _FakeWorkspace.fail_initialize = True
        manager = E2BWorkspaceManager(prewarm=PrewarmConfig(size=1))
        manager._start_prewarm()
        await asyncio.sleep(0.05)

        self.assertEqual(len(_FakeWorkspace.created), 1)
        self.assertTrue(_FakeWorkspace.created[0].sandbox.killed)
