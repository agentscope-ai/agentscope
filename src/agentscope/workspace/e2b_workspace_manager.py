# -*- coding: utf-8 -*-
"""E2BWorkspaceManager — manages :class:`E2BWorkspace` instances.

Creates E2B cloud-sandbox workspaces with shared default configuration.

Usage::

    manager = E2BWorkspaceManager(
        template="my-template",
        api_key="e2b-api-key",
        default_mcp_servers=[
            MCPServerConfig(name="fs", command="mcp-server-fs"),
        ],
    )
    await manager.initialize()

    workspace = await manager.get_workspace("user_1", "agent_1")

Pool usage (RL rollout)::

    manager = E2BWorkspaceManager(template="my-template")
    manager.enable_pool(capacity=8)
    await manager.initialize()
    await manager.warm_up_pool()

    ws = await manager.acquire_from_pool()
    # ... rollout ...
    await manager.release_to_pool(ws)
"""

import asyncio
from typing import Any

from .workspace_manager_base import WorkspaceManagerBase
from .workspace_base import WorkspaceBase
from .e2b_workspace import E2BWorkspace
from .config import MCPServerConfig
from .types import SerializedWorkspaceState
from .._logging import logger


class E2BWorkspaceManager(WorkspaceManagerBase):
    """Manages E2B cloud-sandbox workspaces."""

    def __init__(
        self,
        template: str = E2BWorkspace.DEFAULT_TEMPLATE,
        api_key: str = "",
        domain: str = "",
        timeout: int = E2BWorkspace.DEFAULT_TIMEOUT,
        working_dir: str = E2BWorkspace.DEFAULT_WORKING_DIR,
        default_mcp_servers: list[MCPServerConfig] | None = None,
        gateway_port: int = E2BWorkspace.GATEWAY_PORT,
        default_env: dict[str, str] | None = None,
        default_metadata: dict[str, str] | None = None,
        default_startup_commands: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._template = template
        self._api_key = api_key
        self._domain = domain
        self._timeout = timeout
        self._working_dir = working_dir
        self._default_mcp_servers = list(default_mcp_servers or [])
        self._gateway_port = gateway_port
        self._default_env = dict(default_env or {})
        self._default_metadata = dict(default_metadata or {})
        self._default_startup_commands = list(
            default_startup_commands or [],
        )
        self._workspaces: dict[str, E2BWorkspace] = {}

    async def initialize(self) -> None:
        logger.info(
            "E2BWorkspaceManager: initialized (template=%s)",
            self._template,
        )

    async def close(self) -> None:
        tasks = []
        for ws in self._workspaces.values():
            tasks.append(ws.close())
        if tasks:
            results = await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("Error closing workspace: %s", r)
        self._workspaces.clear()
        await self._close_pool()

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        key = f"{user_id}/{agent_id}"
        if key in self._workspaces:
            existing = self._workspaces[key]
            if existing._started:
                return existing

        ws = E2BWorkspace(
            template=self._template,
            api_key=self._api_key,
            domain=self._domain,
            timeout=self._timeout,
            working_dir=self._working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
            env=dict(self._default_env),
            metadata=dict(self._default_metadata),
            startup_commands=list(self._default_startup_commands),
        )
        await ws.initialize()
        self._workspaces[key] = ws
        logger.info(
            "E2BWorkspaceManager: created workspace for %s",
            key,
        )
        return ws

    async def restore(
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        """Restore an E2B workspace by reconnecting to an existing sandbox."""
        from e2b import AsyncSandbox

        sandbox_id = state.payload.get("sandbox_id")
        if not sandbox_id:
            raise ValueError(
                "Cannot restore: 'sandbox_id' missing from state payload",
            )

        working_dir = state.payload.get(
            "working_dir",
            E2BWorkspace.DEFAULT_WORKING_DIR,
        )
        api_key = state.payload.get("api_key", self._api_key)
        domain = state.payload.get("domain", self._domain)

        connect_kwargs: dict[str, Any] = {"sandbox_id": sandbox_id}
        if api_key:
            connect_kwargs["api_key"] = api_key
        if domain:
            connect_kwargs["domain"] = domain

        sandbox = await AsyncSandbox.connect(**connect_kwargs)

        ws = E2BWorkspace(
            api_key=api_key,
            domain=domain,
            working_dir=working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
        )
        ws._sandbox = sandbox
        ws._id = state.payload.get("workspace_id", ws._id)

        if ws._mcp_servers:
            await ws._start_gateway()

        ws._started = True
        logger.info("E2BWorkspaceManager: restored workspace %s", ws._id)
        return ws

    async def _create_for_pool(self) -> WorkspaceBase:
        ws = E2BWorkspace(
            template=self._template,
            api_key=self._api_key,
            domain=self._domain,
            timeout=self._timeout,
            working_dir=self._working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
            env=dict(self._default_env),
            metadata=dict(self._default_metadata),
            startup_commands=list(self._default_startup_commands),
        )
        await ws.initialize()
        return ws
