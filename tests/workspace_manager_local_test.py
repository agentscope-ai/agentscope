# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Security and lifecycle tests for LocalWorkspaceManager."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    IsolationPolicy,
    LocalWorkspaceManager,
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
        """Mark the workspace initialized."""
        await asyncio.sleep(0)
        Path(str(self.kwargs["workdir"])).mkdir(
            parents=True,
            exist_ok=True,
        )
        self.initialized = True

    async def close(self) -> None:
        """Mark the workspace closed."""
        self.closed = True


class TestLocalWorkspaceManager(IsolatedAsyncioTestCase):
    """Validate user isolation, cache behavior, and path safety."""

    async def asyncSetUp(self) -> None:
        """Patch LocalWorkspace and allocate a temporary base directory."""
        _FakeWorkspace.created.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_local_workspace_manager.LocalWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()

    async def asyncTearDown(self) -> None:
        """Undo patches and remove the temporary directory."""
        self.workspace_patch.stop()
        self.temp_dir.cleanup()

    async def test_shared_agent_is_isolated_between_users(self) -> None:
        """A shared agent must not share its implicit local workspace."""
        manager = LocalWorkspaceManager(self.temp_dir.name)

        owner = await manager.get_workspace(
            "owner",
            "shared-agent",
            "owner-session",
        )
        viewer = await manager.get_workspace(
            "viewer",
            "shared-agent",
            "viewer-session",
        )

        self.assertIsNot(owner, viewer)
        self.assertNotEqual(owner.workspace_id, viewer.workspace_id)
        self.assertNotEqual(owner.kwargs["workdir"], viewer.kwargs["workdir"])
        self.assertEqual(len(_FakeWorkspace.created), 2)

        owner_secret = Path(str(owner.kwargs["workdir"])) / "secret.txt"
        owner_secret.write_text("owner only", encoding="utf-8")
        viewer_secret = Path(str(viewer.kwargs["workdir"])) / "secret.txt"
        self.assertFalse(viewer_secret.exists())

    async def test_explicit_workspace_id_is_user_scoped(self) -> None:
        """An explicit id is not a bearer key across user boundaries."""
        manager = LocalWorkspaceManager(self.temp_dir.name)

        owner = await manager.get_workspace(
            "owner",
            "shared-agent",
            "s1",
            "shared-workspace",
        )
        viewer = await manager.get_workspace(
            "viewer",
            "shared-agent",
            "s2",
            "shared-workspace",
        )

        self.assertIsNot(owner, viewer)
        self.assertNotEqual(owner.kwargs["workdir"], viewer.kwargs["workdir"])
        self.assertIn(("owner", "shared-workspace"), manager._cache)
        self.assertIn(("viewer", "shared-workspace"), manager._cache)

    async def test_same_user_can_share_workspace_across_agents(self) -> None:
        """Team agents for one user can intentionally share one id."""
        manager = LocalWorkspaceManager(self.temp_dir.name)

        leader = await manager.get_workspace(
            "user",
            "leader",
            "s1",
            "team-workspace",
        )
        member = await manager.get_workspace(
            "user",
            "member",
            "s2",
            "team-workspace",
        )

        self.assertIs(leader, member)
        self.assertEqual(len(_FakeWorkspace.created), 1)

    async def test_missing_workspace_id_uses_real_scope_values(self) -> None:
        """Fallback id assignment receives the caller's full scope."""
        manager = LocalWorkspaceManager(
            self.temp_dir.name,
            isolation=IsolationPolicy.PER_SESSION,
        )

        with patch.object(
            manager,
            "assign_workspace_id",
            return_value="generated-workspace",
        ) as assign_workspace_id:
            workspace = await manager.get_workspace(
                "user",
                "agent",
                "session",
            )

        assign_workspace_id.assert_called_once_with(
            user_id="user",
            agent_id="agent",
            session_id="session",
        )
        self.assertEqual(workspace.workspace_id, "generated-workspace")

    async def test_identifiers_cannot_escape_basedir(self) -> None:
        """Hostile identifiers are hashed before entering a local path."""
        manager = LocalWorkspaceManager(self.temp_dir.name)

        workspace = await manager.get_workspace(
            "../../other-user",
            "ignored-agent",
            "session",
            "../outside/workspace",
        )

        basedir = Path(self.temp_dir.name).resolve()
        workdir = Path(str(workspace.kwargs["workdir"])).resolve()
        self.assertEqual(os.path.commonpath((basedir, workdir)), str(basedir))
        relative = workdir.relative_to(basedir)
        self.assertEqual(len(relative.parts), 2)
        for component in relative.parts:
            self.assertRegex(component, r"^[0-9a-f]{32}$")

    async def test_deprecated_create_workspace_keeps_user_isolation(
        self,
    ) -> None:
        """The compatibility API delegates to the isolated code path."""
        manager = LocalWorkspaceManager(self.temp_dir.name)

        owner = await manager.create_workspace("owner", "agent", "s1")
        viewer = await manager.create_workspace("viewer", "agent", "s2")

        self.assertIsNot(owner, viewer)
        self.assertNotEqual(owner.kwargs["workdir"], viewer.kwargs["workdir"])

    async def test_ttl_eviction_handles_scoped_cache_keys(self) -> None:
        """TTL eviction closes entries after the cache key migration."""
        manager = LocalWorkspaceManager(self.temp_dir.name, ttl=-1)
        expired = await manager.get_workspace("u1", "a", "s", "old")

        active = await manager.get_workspace("u2", "a", "s", "new")

        self.assertTrue(expired.closed)
        self.assertFalse(active.closed)
        self.assertNotIn(("u1", "old"), manager._cache)
        self.assertIn(("u2", "new"), manager._cache)

    async def test_close_evicts_all_scopes_for_workspace_id(self) -> None:
        """The legacy close signature handles every matching tuple key."""
        manager = LocalWorkspaceManager(self.temp_dir.name)
        first = await manager.get_workspace("u1", "a", "s", "shared")
        second = await manager.get_workspace("u2", "a", "s", "shared")
        remaining = await manager.get_workspace("u1", "a", "s", "other")

        await manager.close("shared")

        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertFalse(remaining.closed)
        self.assertNotIn(("u1", "shared"), manager._cache)
        self.assertNotIn(("u2", "shared"), manager._cache)
        self.assertIn(("u1", "other"), manager._cache)


if __name__ == "__main__":
    unittest.main()
