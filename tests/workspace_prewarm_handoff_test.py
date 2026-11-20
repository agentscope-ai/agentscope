# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Cancellation at each prewarm ownership handoff."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from agentscope.app.workspace_manager._prewarm import PrewarmConfig
from workspace_prewarm_test import _Manager, _Workspace


async def _assert_lifecycle(
    when: str,
) -> None:
    """Reclaim cancelled handoffs exactly once and preserve successful ones."""
    entered = asyncio.Event()
    release = asyncio.Event()
    adopting = asyncio.Event()
    adopted_release = asyncio.Event()
    disposed = []

    class Manager(_Manager):
        """Control build completion and cache adoption independently."""

        sequence = 0

        async def _create_prewarmed(self) -> _Workspace:
            """Queue cancellation just before delivery to the waiting task."""
            index = self.sequence
            self.sequence += 1
            if index == 0:
                entered.set()
                await release.wait()
                if when == "ready":
                    asyncio.get_running_loop().call_soon(taker.cancel)
            name = f"ws-{index}"
            self.built.append(name)
            return _Workspace(name, self.built)

        async def _adopt_prewarmed(self, workspace: _Workspace) -> None:
            """Expose cancellation while the manager takes ownership."""
            if when == "adopting":
                adopting.set()
                await adopted_release.wait()
            await super()._adopt_prewarmed(workspace)

        async def _dispose_prewarmed(self, workspace: _Workspace) -> None:
            """Record each disposal to detect both leaks and double-close."""
            disposed.append(workspace.workspace_id)
            await super()._dispose_prewarmed(workspace)

    manager = Manager(prewarm=PrewarmConfig(size=1))
    manager._start_prewarm()
    await asyncio.wait_for(entered.wait(), 5)
    taker = asyncio.create_task(manager._mint_workspace_id())
    await asyncio.sleep(0)
    try:
        if when == "pending":
            taker.cancel()
        release.set()
        if when == "adopting":
            await asyncio.wait_for(adopting.wait(), 5)
            taker.cancel()
        result = (await asyncio.gather(taker, return_exceptions=True))[0]
        await asyncio.gather(*list(manager._prewarm_tasks))
        if when == "success":
            assert result == "ws-0"
            assert manager.adopted == ["ws-0"]
            assert "ws-0" not in disposed
        else:
            assert isinstance(result, asyncio.CancelledError)
            assert not manager.adopted
            assert disposed.count("ws-0") == 1
            assert "ws-0" not in manager.built
        assert len(manager._slots) == 1
    finally:
        release.set()
        adopted_release.set()
        await asyncio.gather(taker, return_exceptions=True)
        await manager._stop_prewarm()


class TestLifecycle(IsolatedAsyncioTestCase):
    """Exercise the production lifecycle at controlled handoff points."""

    async def test_handoff_outcomes(self) -> None:
        """Preserve successful work and reclaim interrupted acquisition."""
        for outcome in ["pending", "ready", "adopting", "success"]:
            with self.subTest(outcome=outcome):
                await _assert_lifecycle(outcome)
