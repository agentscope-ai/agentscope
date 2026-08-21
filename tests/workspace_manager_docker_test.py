# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DockerWorkspaceManager`."""

import asyncio
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.workspace_manager import (
    PrewarmConfig,
    DockerWorkspaceManager,
    IsolationPolicy,
)


class _FakeWorkspace:
    """Workspace double used by manager tests."""

    created: list["_FakeWorkspace"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.workspace_id = str(kwargs.get("workspace_id") or "new-id")
        self.closed = False
        _FakeWorkspace.created.append(self)

    async def initialize(self) -> None:
        """Yield once so builds can interleave."""
        await asyncio.sleep(0)

    async def close(self) -> None:
        """Mark closed."""
        self.closed = True


class TestDockerWorkspaceManager(IsolatedAsyncioTestCase):
    """Workdir layout and the pre-warm hand-off."""

    async def asyncSetUp(self) -> None:
        """Patch the workspace class and give the manager a basedir."""
        _FakeWorkspace.created.clear()
        self.workspace_patch = patch(
            "agentscope.app.workspace_manager."
            "_docker_workspace_manager.DockerWorkspace",
            _FakeWorkspace,
        )
        self.workspace_patch.start()
        self.basedir = tempfile.mkdtemp()

    async def asyncTearDown(self) -> None:
        """Undo patches."""
        self.workspace_patch.stop()

    async def test_workdir_is_keyed_by_workspace_id(self) -> None:
        """The bind-mounted host dir is named after the workspace id."""
        manager = DockerWorkspaceManager(self.basedir)

        ws = await manager.get_workspace("u1", "a1", "s1", "fixed-id")

        self.assertDictEqual(
            ws.kwargs,
            {
                "workspace_id": "fixed-id",
                "host_workdir": os.path.join(self.basedir, "fixed-id"),
                "base_image": "python:3.11-slim",
                "node_version": "20",
                "extra_pip": [],
                "gateway_port": 5600,
                "env": {},
                "default_mcps": [],
                "skill_paths": [],
            },
        )
        self.assertTrue(os.path.isdir(os.path.join(self.basedir, "fixed-id")))

    async def test_workspace_id_cannot_escape_the_basedir(self) -> None:
        """A caller-supplied id may not point the bind-mount outside."""
        manager = DockerWorkspaceManager(self.basedir)

        for workspace_id in ("../../etc", "/etc", "a/../../../etc"):
            with self.assertRaises(ValueError):
                manager._workdir_for(workspace_id)

    async def test_prewarmed_container_is_handed_over_without_rebuild(
        self,
    ) -> None:
        """The buffered workspace's id becomes the session binding, and
        ``get_workspace`` then answers from the cache."""
        manager = DockerWorkspaceManager(
            self.basedir,
            isolation=IsolationPolicy.PER_SESSION,
            prewarm=PrewarmConfig(size=1),
        )
        manager._start_prewarm()
        await asyncio.sleep(0.05)
        self.assertEqual(len(_FakeWorkspace.created), 1)
        prewarmed = _FakeWorkspace.created[0]

        workspace_id = await manager.assign_workspace_id(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
        )
        ws = await manager.get_workspace("u1", "a1", "s1", workspace_id)

        self.assertEqual(workspace_id, prewarmed.workspace_id)
        self.assertIs(ws, prewarmed)
        # One replacement build, and nothing built for the request itself.
        await asyncio.sleep(0.05)
        self.assertEqual(len(_FakeWorkspace.created), 2)

    async def test_aexit_closes_buffered_and_cached_workspaces(self) -> None:
        """Neither the buffer nor the cache survives shutdown."""
        manager = DockerWorkspaceManager(
            self.basedir,
            prewarm=PrewarmConfig(size=2),
        )
        async with manager:
            await asyncio.sleep(0.05)
            await manager.get_workspace("u1", "a1", "s1", "cached-id")
            self.assertEqual(len(_FakeWorkspace.created), 3)

        self.assertListEqual(
            [ws.closed for ws in _FakeWorkspace.created],
            [True, True, True],
        )
