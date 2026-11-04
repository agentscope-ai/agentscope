# -*- coding: utf-8 -*-
"""Test cases for :class:`LocalWorkspaceManager`."""

from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    IsolationPolicy,
    LocalWorkspaceManager,
)


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")

    async def initialize(self) -> None:
        """No-op — the manager only needs the object back."""

    async def close(self) -> None:
        """No-op."""


class TestLocalWorkspaceManager(IsolatedAsyncioTestCase):
    """The unbound-id fallback honours the isolation policy."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_local_workspace_manager.LocalWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_an_empty_workspace_id_stays_per_user(self) -> None:
        """Sessions persisted with ``workspace_id=""`` derive a binding
        from their own user, not from a blank one — under ``PER_USER``
        a blank owner would pool every user onto a single id."""
        manager = LocalWorkspaceManager(
            "/tmp/local-manager-test",
            isolation=IsolationPolicy.PER_USER,
        )

        alice = await manager.get_workspace("alice", "a1", "s", "")
        bob = await manager.get_workspace("bob", "a2", "s", "")

        self.assertIsNot(alice, bob)
        self.assertListEqual(
            [alice.kwargs["workspace_id"], bob.kwargs["workspace_id"]],
            ["982aa9b33217069a", "883053c3e4594c5b"],
        )


class _CloseTrackingWorkspace(_FakeWorkspace):
    """Workspace double that records which ids were closed."""

    closed: list[str] = []

    async def close(self) -> None:
        """Record the close instead of doing anything."""
        _CloseTrackingWorkspace.closed.append(self.workspace_id)


class TestLocalWorkspaceManagerTtl(IsolatedAsyncioTestCase):
    """Asking for a workspace must not evict that same workspace."""

    async def asyncSetUp(self) -> None:
        """Patch in a workspace double that records closes."""
        _CloseTrackingWorkspace.closed = []
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager"
            "._local_workspace_manager.LocalWorkspace",
            _CloseTrackingWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_a_requested_workspace_is_not_rebuilt(self) -> None:
        """A cached workspace is reused even once it is past its TTL.

        The sweep used to run before the lookup, so it popped the entry the
        caller had just asked for, closed it, and built a replacement.
        """
        # ttl=0.0 makes every entry immediately expired.
        manager = LocalWorkspaceManager("/tmp/local-manager-ttl", ttl=0.0)

        first = await manager.get_workspace("u", "a", "s", "w1")
        self.assertListEqual(_CloseTrackingWorkspace.closed, [])

        second = await manager.get_workspace("u", "a", "s", "w1")

        self.assertIs(second, first)
        self.assertListEqual(_CloseTrackingWorkspace.closed, [])

    async def test_other_idle_workspaces_are_still_evicted(self) -> None:
        """Refreshing one entry does not disable eviction for the rest.

        The clock is pinned so both entries can be past their TTL at the
        same time: the requested one must be refreshed and survive, and the
        other must still be swept.
        """
        clock = {"now": 1000.0}

        with patch(
            "agentscope.app.workspace_manager"
            "._local_workspace_manager.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            manager = LocalWorkspaceManager(
                "/tmp/local-manager-ttl2",
                ttl=10.0,
            )

            # t=1000: w1 is created and cached at 1000.
            first = await manager.get_workspace("u", "a", "s", "w1")

            # t=1001: w2 is created; w1 is only 1s idle, so it survives.
            clock["now"] = 1001.0
            second = await manager.get_workspace("u", "a", "s", "w2")

            # t=1020: w1 has been idle for 20s and w2 for 19s, so both are
            # past the 10s TTL. Asking for w1 must refresh w1 and only w2 is
            # swept.
            clock["now"] = 1020.0
            again = await manager.get_workspace("u", "a", "s", "w1")

        self.assertIs(again, first)
        self.assertIsNot(second, first)
        self.assertListEqual(_CloseTrackingWorkspace.closed, ["w2"])
