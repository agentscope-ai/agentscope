# -*- coding: utf-8 -*-
"""Tests for ``DELETE /workspace/skill/{name}``.

The sandboxed workspaces (Docker, E2B, K8s, …) all inherit
:meth:`WorkspaceBase.remove_skill`, which raises ``KeyError`` for an
unknown name. The route must treat that as an idempotent deletion,
matching :class:`LocalWorkspace`, instead of letting it surface as a 500.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Any
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router._workspace import workspace_router
from agentscope.app.deps import get_current_user_id, get_workspace_service
from agentscope.skill import Skill
from agentscope.tool import LocalBackend
from agentscope.workspace import DockerWorkspace, LocalWorkspace, WorkspaceBase

SKILL_NAME = "greeter"

SKILL_MD = """---
name: greeter
description: Greet the user politely.
---

Say hello.
"""


class _WorkspaceService:
    """Stand-in for :class:`WorkspaceService` with one live workspace."""

    def __init__(self, workspace: WorkspaceBase) -> None:
        self._workspace = workspace

    async def resolve(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> WorkspaceBase:
        """Return the workspace the session is bound to."""
        _ = (user_id, agent_id, session_id)
        return self._workspace


class WorkspaceSkillRemoveTest(TestCase):
    """Delete skills through the HTTP route."""

    def setUp(self) -> None:
        """Bind a :class:`DockerWorkspace` to a host directory.

        The workspace is never started, so no container is created; a
        real :class:`LocalBackend` stands in for the sandbox transport
        and the already-equipped partition marker stands in for the
        provisioning step, which is what keeps this test away from the
        ``python3``-only sandbox shim.
        """
        self.workdir = tempfile.mkdtemp()
        workspace = DockerWorkspace()
        workspace.workdir = self.workdir
        # pylint: disable=protected-access
        workspace._backend = LocalBackend()
        self.partition = workspace._skill_partition("agent-1")
        workspace._equipped_partitions.add(self.partition)
        self.workspace: WorkspaceBase = workspace

        app = FastAPI()
        app.include_router(workspace_router)
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"

        def _service() -> _WorkspaceService:
            """Hand the route the workspace under test."""
            return _WorkspaceService(self.workspace)

        app.dependency_overrides[get_workspace_service] = _service
        self.client = self.enterContext(
            TestClient(app, raise_server_exceptions=False),
        )

    def tearDown(self) -> None:
        """Remove the workspace directory created for the test."""
        shutil.rmtree(self.workdir)

    def _delete(self, name: str, agent_id: str = "agent-1") -> Any:
        """DELETE ``/workspace/skill/{name}`` for one agent."""
        return self.client.delete(
            f"/workspace/skill/{name}",
            params={"agent_id": agent_id, "session_id": "session-1"},
        )

    def _seed_skill(self, agent_id: str = "agent-1") -> str:
        """Write one ``SKILL.md`` into an agent's partition by hand."""
        # pylint: disable=protected-access
        partition = self.workspace._skill_partition(agent_id)
        self.workspace._equipped_partitions.add(partition)
        skill_dir = os.path.join(partition, SKILL_NAME)
        os.makedirs(skill_dir)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write(SKILL_MD)
        return skill_dir

    def _skills(self, agent_id: str = "agent-1") -> list[Skill]:
        """List what an agent's partition now holds."""
        return asyncio.run(self.workspace.list_skills(agent_id=agent_id))

    def test_unknown_sandbox_skill_is_idempotent(self) -> None:
        """Deleting an absent sandbox skill succeeds without a body."""
        response = self._delete("typo")

        self.assertEqual(
            (response.status_code, response.content),
            (204, b""),
        )

    def test_unknown_local_skill_is_idempotent(self) -> None:
        """Deleting an absent local skill has the same HTTP semantics."""
        self.workspace = LocalWorkspace(workdir=self.workdir)
        asyncio.run(self.workspace.initialize())

        response = self._delete("typo")

        self.assertEqual(
            (response.status_code, response.content),
            (204, b""),
        )

    def test_installed_skill_still_deletes(self) -> None:
        """The new ``except`` must not swallow a real removal."""
        skill_dir = self._seed_skill()
        self.assertEqual([s.name for s in self._skills()], [SKILL_NAME])

        response = self._delete(SKILL_NAME)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._skills(), [])
        self.assertFalse(os.path.isdir(skill_dir))


if __name__ == "__main__":
    import unittest

    unittest.main()
