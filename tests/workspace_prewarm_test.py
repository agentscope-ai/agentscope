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
from agentscope.app.workspace_manager._prewarm import (
    PrewarmConfig,
    WorkspacePrewarmMixin,
)


class _Workspace:
    """Workspace double. A plain class, so it stays hashable."""

    def __init__(self, workspace_id: str, alive: list[str]) -> None:
        """Track this double in the manager's ``built`` list."""
        self.workspace_id = workspace_id
        self._alive = alive

    async def close(self) -> None:
        """Drop the double from the manager's ``built`` list."""
        self._alive.remove(self.workspace_id)


class _Manager(WorkspacePrewarmMixin, WorkspaceManagerBase):
    """Minimal manager exercising only the pre-warm buffer."""

    def __init__(
        self,
        *,
        prewarm: PrewarmConfig | None = None,
        isolation: IsolationPolicy = IsolationPolicy.PER_SESSION,
        build_delay: float = 0.0,
        fail_builds: int = 0,
    ) -> None:
        """Bind the buffer, the isolation policy and the build script."""
        self.build_delay = build_delay
        self.fail_builds = fail_builds
        self.built: list[str] = []
        self.adopted: list[str] = []
        self.concurrent = 0
        self.peak_concurrent = 0
        WorkspacePrewarmMixin.__init__(self, prewarm=prewarm)
        WorkspaceManagerBase.__init__(self, isolation=isolation)

    async def _create_prewarmed(self) -> _Workspace:
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
            return _Workspace(workspace_id, self.built)
        finally:
            self.concurrent -= 1

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
        manager = _Manager(prewarm=PrewarmConfig(size=2))
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
        manager = _Manager(
            prewarm=PrewarmConfig(size=2, max_creating=3),
            build_delay=0.05,
        )
        manager._start_prewarm()
        await asyncio.sleep(0.2)

        ids = list(
            await asyncio.gather(
                *(
                    manager.assign_workspace_id(
                        user_id="u",
                        agent_id="a",
                        session_id=f"s{i}",
                    )
                    for i in range(10)
                ),
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
        manager = _Manager(prewarm=PrewarmConfig(size=1), fail_builds=5)
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

    async def test_waiter_survives_a_build_that_fails_under_it(
        self,
    ) -> None:
        """A slot that fails while someone waits on it resolves rather
        than hanging, and the waiter falls back to an ordinary id."""
        manager = _Manager(
            prewarm=PrewarmConfig(size=1),
            build_delay=0.05,
            fail_builds=5,
        )
        manager._start_prewarm()

        workspace_id = await asyncio.wait_for(
            manager.assign_workspace_id(
                user_id="u",
                agent_id="a",
                session_id="s",
            ),
            timeout=2,
        )

        self.assertListEqual(manager.built, [])
        self.assertListEqual(manager.adopted, [])
        self.assertNotIn(workspace_id, ("", None))

    async def test_stop_closes_buffered_workspaces(self) -> None:
        """Shutdown drains the buffer instead of leaking sandboxes."""
        manager = _Manager(prewarm=PrewarmConfig(size=3))
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        self.assertListEqual(manager.built, ["ws-0", "ws-1", "ws-2"])

        await manager._stop_prewarm()

        self.assertListEqual(manager.built, [])
        self.assertEqual(len(manager._slots), 0)

    async def test_per_agent_reuses_the_bound_workspace(self) -> None:
        """A returning ``(user, agent)`` gets its recorded binding back,
        and only a first-time pair draws from the buffer."""
        manager = _Manager(
            prewarm=PrewarmConfig(size=1),
            isolation=IsolationPolicy.PER_AGENT,
        )
        manager._start_prewarm()
        await asyncio.sleep(0.05)

        manager.bind_storage(
            SimpleNamespace(
                list_sessions=self._sessions_returning("bound-ws"),
            ),
        )
        returning = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s2",
        )
        manager.bind_storage(
            SimpleNamespace(list_sessions=self._sessions_returning()),
        )
        first_time = await manager.assign_workspace_id(
            user_id="u",
            agent_id="b",
            session_id="s1",
        )

        self.assertEqual(returning, "bound-ws")
        self.assertEqual(first_time, "ws-0")
        self.assertListEqual(manager.adopted, ["ws-0"])

    async def test_caller_cancellation_is_not_swallowed(self) -> None:
        """Cancelling a request that waits on a build must cancel it,
        not hand back a workspace nobody is there to receive."""
        manager = _Manager(prewarm=PrewarmConfig(size=1), build_delay=0.2)
        manager._start_prewarm()
        task = asyncio.create_task(
            manager.assign_workspace_id(
                user_id="u",
                agent_id="a",
                session_id="s",
            ),
        )
        await asyncio.sleep(0.05)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_a_minted_id_is_held_until_storage_has_it(self) -> None:
        """The session flow persists the binding after this returns, so
        a second request in that window must not mint its own."""
        manager = _Manager(
            prewarm=PrewarmConfig(size=2),
            isolation=IsolationPolicy.PER_AGENT,
        )
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        manager.bind_storage(
            SimpleNamespace(list_sessions=self._sessions_returning()),
        )

        first = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s1",
        )
        second = await manager.assign_workspace_id(
            user_id="u",
            agent_id="a",
            session_id="s2",
        )

        self.assertListEqual([first, second], ["ws-0", "ws-0"])
        self.assertListEqual(manager.adopted, ["ws-0"])

    async def test_concurrent_first_sessions_bind_one_workspace(
        self,
    ) -> None:
        """Two first sessions racing on one ``(user, agent)`` must not
        each mint a workspace."""
        manager = _Manager(
            prewarm=PrewarmConfig(size=2),
            build_delay=0.05,
            isolation=IsolationPolicy.PER_AGENT,
        )
        manager._start_prewarm()
        await asyncio.sleep(0.2)
        bound: list[str] = []
        manager.bind_storage(
            SimpleNamespace(list_sessions=self._sessions_from(bound)),
        )

        async def create_session() -> str:
            workspace_id = await manager.assign_workspace_id(
                user_id="u",
                agent_id="a",
                session_id="s",
            )
            bound.append(workspace_id)
            return workspace_id

        ids = list(await asyncio.gather(create_session(), create_session()))

        self.assertListEqual(ids, ["ws-0", "ws-0"])
        self.assertListEqual(manager.adopted, ["ws-0"])

    @staticmethod
    def _sessions_from(bound: list[str]) -> object:
        """Build a ``list_sessions`` double reading a live binding list."""

        async def list_sessions(
            user_id: str,
            agent_id: str,
        ) -> list[SimpleNamespace]:
            del user_id, agent_id
            return [
                SimpleNamespace(
                    config=SimpleNamespace(workspace_id=workspace_id),
                )
                for workspace_id in bound
            ]

        return list_sessions

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
