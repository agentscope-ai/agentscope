# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for WorkspaceManagerBase and LocalWorkspaceManager."""

import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.workspace import (
    LocalWorkspace,
    LocalWorkspaceManager,
    WorkspaceBase,
    WorkspaceManagerBase,
)


class TestLocalWorkspaceManagerLifecycle(IsolatedAsyncioTestCase):
    """Test LocalWorkspaceManager creation, lookup, and closing."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = LocalWorkspaceManager(basedir=self.temp_dir.name)
        await self.manager.initialize()

    async def asyncTearDown(self) -> None:
        await self.manager.close_all()
        self.temp_dir.cleanup()

    async def test_is_subclass(self) -> None:
        """LocalWorkspaceManager is a WorkspaceManagerBase."""
        self.assertIsInstance(self.manager, WorkspaceManagerBase)

    async def test_create_workspace(self) -> None:
        """create_workspace returns an initialized LocalWorkspace."""
        ws = await self.manager.create_workspace("u1", "agent1", "s1")
        self.assertIsInstance(ws, WorkspaceBase)
        self.assertIsInstance(ws, LocalWorkspace)
        self.assertTrue(await ws.is_alive())

    async def test_deterministic_workdir(self) -> None:
        """Workspace workdir is basedir/agent_id."""
        ws = await self.manager.create_workspace("u1", "myagent", "s1")
        expected = os.path.join(self.temp_dir.name, "myagent")
        self.assertEqual(ws.workdir, expected)
        self.assertTrue(os.path.isdir(expected))

    async def test_get_workspace_by_id(self) -> None:
        """get_workspace finds workspace by workspace_id."""
        ws = await self.manager.create_workspace("u1", "agent1", "s1")
        found = await self.manager.get_workspace(ws.workspace_id)
        self.assertIs(found, ws)

    async def test_get_workspace_nonexistent_returns_none(self) -> None:
        """get_workspace returns None for unknown workspace."""
        result = await self.manager.get_workspace("nonexistent-id")
        self.assertIsNone(result)

    async def test_close_single_workspace(self) -> None:
        """close(workspace_id) removes workspace from tracking."""
        ws = await self.manager.create_workspace("u1", "agent1", "s1")
        wsid = ws.workspace_id

        await self.manager.close(wsid)

        # Should no longer be tracked
        self.assertNotIn(wsid, self.manager._cache)

    async def test_close_all(self) -> None:
        """close_all() clears all tracked workspaces."""
        await self.manager.create_workspace("u1", "agent1", "s1")
        await self.manager.create_workspace("u2", "agent2", "s2")

        await self.manager.close_all()

        self.assertEqual(len(self.manager._cache), 0)
        self.assertEqual(len(self.manager._cache), 0)
        self.assertEqual(len(self.manager._workspaces), 0)

    async def test_list_workspaces(self) -> None:
        """list_workspaces returns all tracked workspace IDs."""
        ws1 = await self.manager.create_workspace("u1", "a1", "s1")
        ws2 = await self.manager.create_workspace("u2", "a2", "s2")

        ids = self.manager.list_workspaces()
        self.assertIn(ws1.workspace_id, ids)
        self.assertIn(ws2.workspace_id, ids)
        self.assertEqual(len(ids), 2)


class TestLocalWorkspaceManagerTTL(IsolatedAsyncioTestCase):
    """Test TTL-based cache eviction in LocalWorkspaceManager."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_expired_workspace_evicted(self) -> None:
        """Workspaces older than TTL are evicted on get_workspace."""
        manager = LocalWorkspaceManager(
            basedir=self.temp_dir.name,
            ttl=0.01,
        )
        await manager.initialize()

        ws = await manager.create_workspace("u1", "agent1", "s1")
        wsid = ws.workspace_id

        import asyncio

        await asyncio.sleep(0.05)

        # Eviction happens lazily; the old workspace_id is gone
        result = await manager.get_workspace(wsid)
        self.assertIsNone(result)

        await manager.close_all()

    async def test_fresh_workspace_not_evicted(self) -> None:
        """Workspaces within TTL are not evicted."""
        manager = LocalWorkspaceManager(
            basedir=self.temp_dir.name,
            ttl=3600.0,
        )
        await manager.initialize()

        ws = await manager.create_workspace("u1", "agent1", "s1")

        result = await manager.get_workspace(ws.workspace_id)
        self.assertIs(result, ws)

        await manager.close_all()


class TestLocalWorkspaceManagerContextManager(IsolatedAsyncioTestCase):
    """Test async context manager protocol."""

    async def test_context_manager(self) -> None:
        """async with LocalWorkspaceManager works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with LocalWorkspaceManager(
                basedir=tmpdir,
            ) as manager:
                ws = await manager.create_workspace(
                    "u1",
                    "agent1",
                    "s1",
                )
                self.assertIsInstance(ws, LocalWorkspace)


class TestLocalWorkspaceManagerRestore(IsolatedAsyncioTestCase):
    """Test workspace restore functionality."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_restore_from_state(self) -> None:
        """restore() reconnects from serialized state."""
        manager = LocalWorkspaceManager(basedir=self.temp_dir.name)
        await manager.initialize()

        ws = await manager.create_workspace("u1", "agent1", "s1")
        state = await ws.export_state()

        # Create a new manager and restore
        manager2 = LocalWorkspaceManager(basedir=self.temp_dir.name)
        await manager2.initialize()

        restored = await manager2.restore(state)
        self.assertIsInstance(restored, LocalWorkspace)
        self.assertEqual(restored.workdir, ws.workdir)

        await manager.close_all()
        await manager2.close_all()

    async def test_restore_missing_workdir_raises(self) -> None:
        """restore() raises ValueError if workdir is missing."""
        from agentscope.workspace.types import SerializedWorkspaceState

        manager = LocalWorkspaceManager(basedir=self.temp_dir.name)
        await manager.initialize()

        state = SerializedWorkspaceState(
            backend_type="local",
            payload={},
        )
        with self.assertRaises(ValueError):
            await manager.restore(state)

        await manager.close_all()


class TestWorkspaceManagerWorkspaceIsolation(IsolatedAsyncioTestCase):
    """Test that different (user, agent, session) produce isolated workdirs."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = LocalWorkspaceManager(basedir=self.temp_dir.name)
        await self.manager.initialize()

    async def asyncTearDown(self) -> None:
        await self.manager.close_all()
        self.temp_dir.cleanup()

    async def test_different_agents_different_workdirs(self) -> None:
        """Different agent_ids produce different workdirs."""
        ws1 = await self.manager.create_workspace("u1", "a1", "s1")
        ws2 = await self.manager.create_workspace("u2", "a2", "s2")
        self.assertIsNot(ws1, ws2)
        self.assertNotEqual(ws1.workspace_id, ws2.workspace_id)
        self.assertNotEqual(ws1.workdir, ws2.workdir)

    async def test_lookup_by_workspace_id(self) -> None:
        """Lookup by workspace_id works after create."""
        ws = await self.manager.create_workspace("u1", "a1", "s1")
        found = await self.manager.get_workspace(ws.workspace_id)
        self.assertIs(found, ws)

    async def test_close_removes_workspace(self) -> None:
        """close() removes workspace from tracking."""
        ws = await self.manager.create_workspace("u1", "a1", "s1")
        wsid = ws.workspace_id
        await self.manager.close(wsid)

        found = await self.manager.get_workspace(wsid)
        self.assertIsNone(found)
