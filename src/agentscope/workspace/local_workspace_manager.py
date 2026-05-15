# -*- coding: utf-8 -*-
"""LocalWorkspaceManager — manages :class:`LocalWorkspace` instances.

Creates isolated local directories per user/agent pair. Suitable for
single-machine deployments where no container isolation is needed.

Usage::

    manager = LocalWorkspaceManager(
        base_dir="/data/workspaces",
        default_skill_paths=["/skills/web-search"],
    )
    await manager.initialize()

    workspace = await manager.get_workspace("user_1", "agent_1")
    # workspace.workdir → /data/workspaces/user_1/agent_1
"""

import os
from typing import Any

from .workspace_manager_base import WorkspaceManagerBase
from .workspace_base import WorkspaceBase
from .local_workspace import LocalWorkspace
from .types import SerializedWorkspaceState
from ..mcp import MCPClient
from .._logging import logger


class LocalWorkspaceManager(WorkspaceManagerBase):
    """Manages local-filesystem workspaces."""

    def __init__(
        self,
        base_dir: str = "/tmp/agentscope_workspaces",  # noqa: S108
        default_skill_paths: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
    ) -> None:
        super().__init__()
        self._base_dir = os.path.abspath(base_dir)
        self._default_skill_paths = list(default_skill_paths or [])
        self._default_mcps = list(default_mcps or [])
        self._workspaces: dict[str, LocalWorkspace] = {}

    async def initialize(self) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        logger.info(
            "LocalWorkspaceManager: initialized at %s",
            self._base_dir,
        )

    async def close(self) -> None:
        for ws in self._workspaces.values():
            try:
                await ws.close()
            except Exception as e:
                logger.warning("Error closing workspace: %s", e)
        self._workspaces.clear()

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        key = f"{user_id}/{agent_id}"
        if key in self._workspaces:
            return self._workspaces[key]

        workdir = os.path.join(self._base_dir, user_id, agent_id)
        ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=self._default_skill_paths,
            mcps=list(self._default_mcps),
        )
        await ws.initialize()
        self._workspaces[key] = ws
        logger.info(
            "LocalWorkspaceManager: created workspace for %s",
            key,
        )
        return ws

    async def restore(
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        workdir = state.payload.get("workdir", "")
        if not workdir:
            raise ValueError(
                "Cannot restore: 'workdir' missing from state payload",
            )
        ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=self._default_skill_paths,
            mcps=list(self._default_mcps),
        )
        await ws.initialize()
        return ws

