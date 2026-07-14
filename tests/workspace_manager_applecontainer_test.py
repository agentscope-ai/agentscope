# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerWorkspaceManager`."""

import asyncio
import sys
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch
from unittest import skipUnless

from agentscope.app.workspace_manager import (
    AppleContainerWorkspaceManager,
    IsolationPolicy,
)


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.initialized = False
        self.closed = False
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Mark initialized."""
        await asyncio.sleep(0)
        self.initialized = True

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


@skipUnless(sys.platform == "darwin", "Apple Container tests require macOS")
class TestAppleContainerWorkspaceManager(IsolatedAsyncioTestCase):
    """Manager cache, config forwarding and TTL behavior."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class used by the manager."""
        _FakeWorkspace.created.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_applecontainer_workspace_manager.AppleContainerWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_default_config(self) -> None:
        """Manager picks up default constants when none are given."""
        manager = AppleContainerWorkspaceManager()
        self.assertEqual(manager._base_image, "python:3.11-slim")
        self.assertEqual(manager._cpus, 2)
        self.assertEqual(manager._memory, "2G")
        self.assertEqual(manager._gateway_port, 5600)
        self.assertEqual(manager._isolation, IsolationPolicy.PER_AGENT)

    async def test_custom_config(self) -> None:
        """All config knobs are forwarded to new workspaces."""
        manager = AppleContainerWorkspaceManager(
            base_image="ubuntu:latest",
            cpus=4,
            memory="8G",
            gateway_port=9999,
            isolation=IsolationPolicy.PER_SESSION,
        )

        ws = await manager.get_workspace(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )
        self.assertEqual(ws.kwargs["base_image"], "ubuntu:latest")
        self.assertEqual(ws.kwargs["cpus"], 4)
        self.assertEqual(ws.kwargs["memory"], "8G")
        self.assertEqual(ws.kwargs["gateway_port"], 9999)

    async def test_get_workspace_caches(self) -> None:
        """Same workspace_id returns the cached instance."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
        )

        ws1 = await manager.get_workspace(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            workspace_id="fixed-id",
        )
        ws2 = await manager.get_workspace(
            user_id="u2",
            agent_id="a2",
            session_id="s2",
            workspace_id="fixed-id",
        )
        self.assertIs(ws1, ws2)
        self.assertEqual(
            len(_FakeWorkspace.created),
            1,
            "Only one workspace should be created on cache hit",
        )

    async def test_close_evicts(self) -> None:
        """close() pops the workspace from cache and calls its close."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
        )
        ws = await manager.get_workspace(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            workspace_id="fixed-id",
        )
        self.assertTrue(ws.initialized)  # type: ignore[attr-defined]

        await manager.close("fixed-id")
        self.assertTrue(ws.closed)  # type: ignore[attr-defined]
        # Subsequent get_workspace with same id creates a new one.
        ws2 = await manager.get_workspace(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            workspace_id="fixed-id",
        )
        self.assertIsNot(ws, ws2)

    async def test_close_all(self) -> None:
        """close_all shuts down every cached workspace."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
        )
        ws1 = await manager.get_workspace(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            workspace_id="id-1",
        )
        ws2 = await manager.get_workspace(
            user_id="u2",
            agent_id="a2",
            session_id="s2",
            workspace_id="id-2",
        )
        await manager.close_all()
        self.assertTrue(ws1.closed)  # type: ignore[attr-defined]
        self.assertTrue(ws2.closed)  # type: ignore[attr-defined]

    async def test_sweeper_evicts_expired(self) -> None:
        """Background sweeper closes and evicts idle workspaces."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
            ttl=0.01,  # expire almost immediately
            sweep_interval=0.01,
        )
        async with manager:
            ws = await manager.get_workspace(
                user_id="u1",
                agent_id="a1",
                session_id="s1",
                workspace_id="sweep-id",
            )
            # Allow the sweeper to fire.
            await asyncio.sleep(0.1)
            # Next get should create a new workspace since the old one
            # expired.
            ws2 = await manager.get_workspace(
                user_id="u1",
                agent_id="a1",
                session_id="s1",
                workspace_id="sweep-id",
            )
        self.assertIsNot(ws, ws2)
        self.assertTrue(ws.closed)  # type: ignore[attr-defined]

    async def test_assign_workspace_id_per_agent(self) -> None:
        """PER_AGENT policy returns deterministic ids."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_AGENT,
        )
        id1 = manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )
        id2 = manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s2",
        )
        # Same user+agent, different session → same id.
        self.assertEqual(id1, id2)

        id3 = manager.assign_workspace_id(
            user_id="u1",
            agent_id="a2",
            session_id="s1",
        )
        # Different agent → different id.
        self.assertNotEqual(id1, id3)

    async def test_assign_workspace_id_per_session(self) -> None:
        """PER_SESSION policy returns unique ids."""
        manager = AppleContainerWorkspaceManager(
            isolation=IsolationPolicy.PER_SESSION,
        )
        id1 = manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )
        id2 = manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s2",
        )
        # Different session → different id.
        self.assertNotEqual(id1, id2)
