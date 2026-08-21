# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`WorkspacePrewarmMixin`."""

import asyncio
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app.workspace_manager._base import (
    IsolationPolicy,
    WorkspaceManagerBase,
)
from agentscope.app.workspace_manager._prewarm import WorkspacePrewarmMixin


class _Manager(WorkspacePrewarmMixin, WorkspaceManagerBase):
    """Minimal manager exercising only the pre-warm buffer."""

    def __init__(self, **kwargs: object) -> None:
        """Bind the buffer, the isolation policy and the build script."""
        isolation = kwargs.pop("isolation", IsolationPolicy.PER_SESSION)
        self.build_delay: float = float(kwargs.pop("build_delay", 0.0))
        self.fail_builds: int = int(kwargs.pop("fail_builds", 0))
        self.built: list[str] = []
        self.adopted: list[str] = []
        self.concurrent = 0
        self.peak_concurrent = 0
        WorkspacePrewarmMixin.__init__(self, **kwargs)  # type: ignore[arg-type]
        WorkspaceManagerBase.__init__(self, isolation=isolation)

    async def _create_prewarmed(self) -> SimpleNamespace:
        """Build a workspace double, tracking build concurrency."""
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            await asyncio.sleep(self.build_delay)
            if self.fail_builds > 0:
                self.fail_builds -= 1
                raise RuntimeError("provider down")
            workspace_id = f"ws-{len(self.built)}"
            self.built.append(workspace_id)
            return SimpleNamespace(
                workspace_id=workspace_id,
                closed=False,
                close=self._close_double(workspace_id),
            )
        finally:
            self.concurrent -= 1

    def _close_double(self, workspace_id: str) -> object:
        """Build the ``close`` coroutine function for a double."""

        async def close() -> None:
            self.built.remove(workspace_id)

        return close

    async def _adopt_prewarmed(self, workspace: object) -> None:
        """Record the hand-off."""
        self.adopted.append(workspace.workspace_id)

    async def get_workspace(self, *args: object, **kwargs: object) -> object:
        """Unused by these tests."""

    async def close(self, workspace_id: str) -> None:
        """Unused by these tests."""

    async def close_all(self) -> None:
        """Unused by these tests."""


class TestWorkspacePrewarm(IsolatedAsyncioTestCase):
    """Buffer filling, hand-off, burst behaviour and shutdown."""

    async def test_disabled_by_default(self) -> None:
        """``prewarm=0`` builds nothing and mints a plain id."""
        manager = _Manager()
        manager._start_prewarm()
        await asyncio.sleep(0)

        workspace_id = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s",
        )

        self.assertListEqual(manager.built, [])
        self.assertListEqual(manager.adopted, [])
        self.assertNotIn(workspace_id, ("", None))

    async def test_buffer_fills_and_hands_out_prebuilt(self) -> None:
        """A ready slot is handed out and immediately replaced."""
        manager = _Manager(prewarm=2)
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        self.assertListEqual(manager.built, ["ws-0", "ws-1"])

        workspace_id = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s",
        )
        await asyncio.sleep(0.05)

        self.assertEqual(workspace_id, "ws-0")
        self.assertListEqual(manager.adopted, ["ws-0"])
        self.assertListEqual(manager.built, ["ws-0", "ws-1", "ws-2"])
        self.assertEqual(len(manager._slots), 2)

    async def test_burst_waits_on_in_flight_builds(self) -> None:
        """Every request is served from the buffer, bounded by
        ``max_creating``, and no request starts a build of its own."""
        manager = _Manager(prewarm=2, max_creating=3, build_delay=0.05)
        manager._start_prewarm()
        await asyncio.sleep(0.2)

        ids = await asyncio.gather(
            *(
                manager.assign_workspace_id(
                    user_id="u",
                    agent_id="a",
                    session_id=f"s{i}",
                )
                for i in range(10)
            ),
        )

        self.assertListEqual(
            sorted(ids),
            [
                "ws-0",
                "ws-1",
                "ws-2",
                "ws-3",
                "ws-4",
                "ws-5",
                "ws-6",
                "ws-7",
                "ws-8",
                "ws-9",
            ],
        )
        self.assertListEqual(manager.adopted, ids)
        self.assertLessEqual(manager.peak_concurrent, 3)

    async def test_failed_build_falls_back_to_plain_id(self) -> None:
        """A starved buffer mints an ordinary id instead of raising."""
        manager = _Manager(prewarm=1, fail_builds=5)
        manager._start_prewarm()
        await asyncio.sleep(0.05)

        workspace_id = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s",
        )

        self.assertListEqual(manager.built, [])
        self.assertListEqual(manager.adopted, [])
        self.assertNotIn(workspace_id, ("", None))

    async def test_stop_closes_buffered_workspaces(self) -> None:
        """Shutdown drains the buffer instead of leaking sandboxes."""
        manager = _Manager(prewarm=3)
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        self.assertListEqual(manager.built, ["ws-0", "ws-1", "ws-2"])

        await manager._stop_prewarm()

        self.assertListEqual(manager.built, [])
        self.assertEqual(len(manager._slots), 0)

    async def test_per_agent_reuses_the_bound_workspace(self) -> None:
        """A returning ``(user, agent)`` gets its recorded binding back,
        and only a first-time pair draws from the buffer."""
        manager = _Manager(prewarm=1, isolation=IsolationPolicy.PER_AGENT)
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        storage = SimpleNamespace(
            list_sessions=self._sessions_returning("bound-ws"),
        )

        returning = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s2",
            storage=storage,
        )
        first_time = await manager.assign_workspace_id(
            user_id="u",
            agent_id="b",
            session_id="s1",
            storage=SimpleNamespace(list_sessions=self._sessions_returning()),
        )

        self.assertEqual(returning, "bound-ws")
        self.assertEqual(first_time, "ws-0")
        self.assertListEqual(manager.adopted, ["ws-0"])

    @staticmethod
    def _sessions_returning(*workspace_ids: str) -> object:
        """Build a ``list_sessions`` double yielding those bindings."""

        async def list_sessions(
            user_id: str,
            agent_id: str,
        ) -> list[SimpleNamespace]:
            del user_id, agent_id
            return [
                SimpleNamespace(
                    config=SimpleNamespace(workspace_id=workspace_id),
                )
                for workspace_id in workspace_ids
            ]

        return list_sessions
