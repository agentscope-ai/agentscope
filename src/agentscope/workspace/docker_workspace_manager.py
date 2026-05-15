# -*- coding: utf-8 -*-
"""DockerWorkspaceManager — manages :class:`DockerWorkspace` instances.

Creates Docker-container workspaces with shared default configuration.
Handles lifecycle for agent-service deployments.

Usage::

    manager = DockerWorkspaceManager(
        image="my-agent-image:latest",
        default_mcp_servers=[
            MCPServerConfig(name="fs", command="mcp-server-fs"),
        ],
    )
    await manager.initialize()

    workspace = await manager.get_workspace("user_1", "agent_1")

Pool usage (RL rollout)::

    manager = DockerWorkspaceManager(image="my-image")
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
from .docker_workspace import DockerWorkspace
from .config import MCPServerConfig
from .types import SerializedWorkspaceState
from .._logging import logger


class DockerWorkspaceManager(WorkspaceManagerBase):
    """Manages Docker-container workspaces."""

    def __init__(
        self,
        image: str = DockerWorkspace.DEFAULT_IMAGE,
        working_dir: str = DockerWorkspace.DEFAULT_WORKING_DIR,
        default_mcp_servers: list[MCPServerConfig] | None = None,
        gateway_port: int = DockerWorkspace.GATEWAY_PORT,
        default_env: dict[str, str] | None = None,
        default_startup_commands: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._image = image
        self._working_dir = working_dir
        self._default_mcp_servers = list(default_mcp_servers or [])
        self._gateway_port = gateway_port
        self._default_env = dict(default_env or {})
        self._default_startup_commands = list(
            default_startup_commands or [],
        )
        self._workspaces: dict[str, DockerWorkspace] = {}

    async def initialize(self) -> None:
        logger.info(
            "DockerWorkspaceManager: initialized (image=%s)",
            self._image,
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

        ws = DockerWorkspace(
            image=self._image,
            working_dir=self._working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
            env=dict(self._default_env),
            startup_commands=list(self._default_startup_commands),
        )
        await ws.initialize()
        self._workspaces[key] = ws
        logger.info(
            "DockerWorkspaceManager: created workspace for %s",
            key,
        )
        return ws

    async def restore(
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        """Restore a Docker workspace from serialized state.

        Reconnects to an existing container by its container_id.
        """
        import docker
        import docker.errors as docker_errors

        container_id = state.payload.get("container_id")
        if not container_id:
            raise ValueError(
                "Cannot restore: 'container_id' missing from state payload",
            )

        working_dir = state.payload.get(
            "working_dir",
            DockerWorkspace.DEFAULT_WORKING_DIR,
        )
        ws_id = state.payload.get("workspace_id", "")
        image = state.payload.get("image", self._image)
        gw_port = state.payload.get("gateway_port", self._gateway_port)

        mcp_cfgs = []
        for s in state.payload.get("mcp_servers", []):
            mcp_cfgs.append(
                MCPServerConfig(
                    name=s["name"],
                    transport=s.get("transport", "stdio"),
                    command=s.get("command", ""),
                    args=s.get("args", []),
                    url=s.get("url", ""),
                ),
            )

        ws = DockerWorkspace(
            image=image,
            working_dir=working_dir,
            mcp_servers=mcp_cfgs or list(self._default_mcp_servers),
            gateway_port=gw_port,
        )

        client = docker.from_env()
        try:
            container = client.containers.get(container_id)
        except docker_errors.NotFound as e:
            client.close()
            raise ValueError(
                f"Container {container_id} not found",
            ) from e

        if container.status != "running":
            container.start()

        ws._client = client
        ws._container = container
        ws._id = ws_id or ws._id

        container.reload()
        attrs = getattr(container, "attrs", {}) or {}
        ports_info = attrs.get("NetworkSettings", {}).get("Ports", {})
        if gw_port:
            bindings = ports_info.get(f"{gw_port}/tcp", [])
            if bindings:
                ws._port_mapping[gw_port] = int(bindings[0]["HostPort"])

        if ws._mcp_servers:
            await ws._start_gateway()

        ws._started = True
        logger.info(
            "DockerWorkspaceManager: restored workspace %s",
            ws._id,
        )
        return ws

    async def _create_for_pool(self) -> WorkspaceBase:
        ws = DockerWorkspace(
            image=self._image,
            working_dir=self._working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
            env=dict(self._default_env),
            startup_commands=list(self._default_startup_commands),
        )
        await ws.initialize()
        return ws
