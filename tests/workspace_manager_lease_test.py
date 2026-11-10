# -*- coding: utf-8 -*-
"""A run leases its workspace so the TTL sweep cannot evict it mid-run."""

import asyncio
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import LocalWorkspaceManager

_TTL = 0.05


class _FakeWorkspace:
    """Workspace double — the lease tests only need identity."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")

    async def initialize(self) -> None:
        """No-op."""

    async def close(self) -> None:
        """No-op."""


class _Storage:
    """Return a session record bound to one workspace id."""

    def __init__(self, workspace_id: str) -> None:
        self._workspace_id = workspace_id

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SimpleNamespace:
        """Ignore the ids and return the bound session."""
        del user_id, agent_id, session_id
        return SimpleNamespace(
            config=SimpleNamespace(workspace_id=self._workspace_id),
        )


class _EmptyStorage:
    """A storage backend with no session for the asked ids."""

    async def get_session(self, *_: object) -> None:
        """Pretend the session was deleted."""
        return None


class TestWorkspaceLease(IsolatedAsyncioTestCase):
    """The cache's last-access time cannot express "still running"."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class and bind a storage backend."""
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_local_workspace_manager.LocalWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()
        self.manager = LocalWorkspaceManager(
            "/tmp/workspace-lease-test",
            ttl=_TTL,
        )
        self.manager.bind_storage(_Storage("ws-a"))

    async def asyncTearDown(self) -> None:
        """Undo the workspace patch."""
        self.workspace_patch.stop()

    async def _let_the_cache_expire(self) -> None:
        """Sleep past the TTL and let an unrelated access sweep."""
        await asyncio.sleep(_TTL * 2)
        await self.manager.get_workspace("u", "a", "other", "ws-other")

    async def test_an_unleased_workspace_is_swept(self) -> None:
        """Baseline: an idle entry is evicted and rebuilt on demand."""
        first = await self.manager.get_workspace("u", "a", "s", "ws-a")

        await self._let_the_cache_expire()

        again = await self.manager.get_workspace("u", "a", "s", "ws-a")
        self.assertIsNot(again, first)

    async def test_a_leased_workspace_survives_the_sweep(self) -> None:
        """A run longer than the TTL keeps the workspace it started on."""
        first = await self.manager.get_workspace("u", "a", "s", "ws-a")

        async with self.manager.hold_session("u", "a", "s"):
            await self._let_the_cache_expire()
            again = await self.manager.get_workspace("u", "a", "s", "ws-a")
            self.assertIs(again, first)

    async def test_the_lease_ends_with_the_run(self) -> None:
        """A workspace is evictable again once its run is over."""
        first = await self.manager.get_workspace("u", "a", "s", "ws-a")

        async with self.manager.hold_session("u", "a", "s"):
            pass

        await self._let_the_cache_expire()

        again = await self.manager.get_workspace("u", "a", "s", "ws-a")
        self.assertIsNot(again, first)

    async def test_concurrent_runs_lease_the_same_workspace(self) -> None:
        """The lease is refcounted, not released by the first run out."""
        first = await self.manager.get_workspace("u", "a", "s", "ws-a")

        async with self.manager.hold_session("u", "a", "s"):
            async with self.manager.hold_session("u", "a", "s"):
                await self._let_the_cache_expire()

            await self._let_the_cache_expire()
            again = await self.manager.get_workspace("u", "a", "s", "ws-a")
            self.assertIs(again, first)

    async def test_a_session_without_a_binding_leases_nothing(self) -> None:
        """Nothing is cached before ``get_workspace`` mints an id, so
        the lease is a no-op rather than an error."""
        manager = LocalWorkspaceManager("/tmp/workspace-lease-test")
        manager.bind_storage(_EmptyStorage())

        async with manager.hold_session("u", "a", "s"):
            self.assertDictEqual(
                manager._held,  # pylint: disable=protected-access
                {},
            )
