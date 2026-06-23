# -*- coding: utf-8 -*-
"""Tests for actor-scoped workspace views and run handles."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.workspace import (
    WorkspaceActor,
    WorkspaceRunHandle,
    WorkspaceView,
)


class _Runtime:
    workspace_id = "workspace-1"
    workdir = "/workspace"

    def __init__(self) -> None:
        self.mcp_scope = None

    async def get_instructions(self) -> str:
        return "workspace instructions"

    async def list_tools(self) -> list[str]:
        return ["tool"]

    async def list_skills(self) -> list[str]:
        return ["skill"]

    async def list_mcps(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> list[Any]:
        self.mcp_scope = (agent_id, session_id)
        return ["mcp"]


class TestWorkspaceView(IsolatedAsyncioTestCase):
    async def test_view_passes_actor_scope_to_mcp_listing(self) -> None:
        runtime = _Runtime()
        view = WorkspaceView(
            runtime=runtime,
            actor=WorkspaceActor(
                user_id="user-1",
                agent_id="agent-1",
                session_id="session-1",
                role="worker",
            ),
        )

        self.assertEqual(await view.list_mcps(), ["mcp"])
        self.assertEqual(runtime.mcp_scope, ("agent-1", "session-1"))
        self.assertEqual(view.workspace_id, "workspace-1")

    async def test_run_handle_release_is_idempotent(self) -> None:
        releases = 0

        async def release() -> None:
            nonlocal releases
            releases += 1

        handle = WorkspaceRunHandle(
            view=WorkspaceView(
                runtime=_Runtime(),
                actor=WorkspaceActor(
                    user_id="user-1",
                    agent_id="agent-1",
                    session_id="session-1",
                ),
            ),
            lease_id="lease-1",
            _release=release,
        )
        await handle.close()
        await handle.close()
        self.assertEqual(releases, 1)
