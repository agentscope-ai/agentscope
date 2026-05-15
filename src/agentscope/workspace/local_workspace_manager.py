# -*- coding: utf-8 -*-
"""LocalWorkspaceManager — manages :class:`LocalWorkspace` instances.

Creates isolated local directories per workspace. Suitable for
single-machine deployments where no container isolation is needed.

Usage::

    manager = LocalWorkspaceManager(base_dir="/data/workspaces")
    await manager.initialize()

    ws = await manager.create_workspace()
    # persist ws.workspace_id in your DB

    # later: look up by workspace_id
    ws = manager.get_workspace(ws_id)
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

    async def initialize(self) -> None:
        os.makedirs(self._base_dir, exist_ok=True)
        logger.info(
            "LocalWorkspaceManager: initialized at %s",
            self._base_dir,
        )

    async def close(self) -> None:
        await self._close_all_workspaces()

    async def _do_create(self, **kwargs: Any) -> WorkspaceBase:
        workdir = kwargs.get("workdir")
        if not workdir:
            import uuid

            workdir = os.path.join(self._base_dir, uuid.uuid4().hex[:12])
        ws = LocalWorkspace(
            workdir=workdir,
            skill_paths=kwargs.get(
                "skill_paths",
                self._default_skill_paths,
            ),
            mcps=list(kwargs.get("mcps", self._default_mcps)),
        )
        await ws.initialize()
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
        self._workspaces[ws.workspace_id] = ws
        return ws
