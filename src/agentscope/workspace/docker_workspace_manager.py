# -*- coding: utf-8 -*-
"""DockerWorkspaceManager — manages :class:`DockerWorkspace` instances.

Creates Docker-container workspaces with shared default configuration.
Handles lifecycle for agent-service deployments.

Usage::

    manager = DockerWorkspaceManager(image="my-agent-image:latest")
    await manager.initialize()

    ws = await manager.create_workspace("u1", "agent-42", "s1")
    ws_id = ws.workspace_id

    ws = await manager.get_workspace(ws_id)

Pool usage (RL rollout)::

    await manager.enable_pool(capacity=8)

    ws = await manager.acquire_from_pool()
    # ... rollout ...
    await manager.release_to_pool(ws)
"""

from typing import Any

from .._logging import logger
from ..mcp import MCPClient
from ..mcp._config import HttpMCPConfig, StdioMCPConfig
from .docker_workspace import DockerWorkspace
from .types import SerializedWorkspaceState
from .workspace_base import WorkspaceBase
from .workspace_manager_base import WorkspaceManagerBase


class DockerWorkspaceManager(WorkspaceManagerBase):
    """Manages Docker-container workspaces."""

    def __init__(
        self,
        image: str = DockerWorkspace.DEFAULT_IMAGE,
        working_dir: str = DockerWorkspace.DEFAULT_WORKING_DIR,
        default_mcp_servers: list[MCPClient] | None = None,
        gateway_port: int = DockerWorkspace.GATEWAY_PORT,
        default_env: dict[str, str] | None = None,
        default_startup_commands: list[str] | None = None,
    ) -> None:
        """Create a Docker workspace manager.

        Args:
            image: Default Docker image for new workspaces.
            working_dir: Default working directory inside containers.
            default_mcp_servers: MCP clients configured in every
                new workspace.
            gateway_port: Default gateway port for new workspaces.
            default_env: Environment variables set in every new
                container.
            default_startup_commands: Shell commands run in every
                new container after creation.
        """
        super().__init__()
        self._image = image
        self._working_dir = working_dir
        self._default_mcp_servers = list(default_mcp_servers or [])
        self._gateway_port = gateway_port
        self._default_env = dict(default_env or {})
        self._default_startup_commands = list(
            default_startup_commands or [],
        )

    async def initialize(self) -> None:
        """Log readiness (Docker client is created lazily per-workspace)."""
        logger.info(
            "DockerWorkspaceManager: initialized (image=%s)",
            self._image,
        )

    async def _do_close(self) -> None:
        """No manager-level resources to release."""

    async def _do_create(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> WorkspaceBase:
        """Create and initialise a new :class:`DockerWorkspace`."""
        ws = DockerWorkspace(
            image=kwargs.get("image", self._image),
            working_dir=kwargs.get(
                "working_dir",
                self._working_dir,
            ),
            mcp_servers=list(
                kwargs.get(
                    "mcp_servers",
                    self._default_mcp_servers,
                ),
            ),
            gateway_port=kwargs.get(
                "gateway_port",
                self._gateway_port,
            ),
            env=dict(kwargs.get("env", self._default_env)),
            startup_commands=list(
                kwargs.get(
                    "startup_commands",
                    self._default_startup_commands,
                ),
            ),
        )
        await ws.initialize()
        return ws

    async def restore(  # pylint: disable=protected-access
        self,
        state: SerializedWorkspaceState,
    ) -> WorkspaceBase:
        """Restore a Docker workspace from serialized state.

        Reconnects to an existing container by its container_id.
        """
        import aiodocker

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

        mcp_cfgs: list[MCPClient] = []
        for s in state.payload.get("mcp_servers", []):
            if s.get("transport") == "http":
                cfg = HttpMCPConfig(url=s.get("url", ""))
            else:
                cfg = StdioMCPConfig(
                    command=s.get("command", ""),
                    args=s.get("args") or None,
                )
            mcp_cfgs.append(
                MCPClient(
                    name=s["name"],
                    is_stateful=True,
                    mcp_config=cfg,
                ),
            )

        ws = DockerWorkspace(
            image=image,
            working_dir=working_dir,
            mcp_servers=mcp_cfgs or list(self._default_mcp_servers),
            gateway_port=gw_port,
        )

        client = aiodocker.Docker()
        try:
            container = await client.containers.get(container_id)
        except aiodocker.exceptions.DockerError as e:
            await client.close()
            raise ValueError(
                f"Container {container_id} not found",
            ) from e

        info = await container.show()
        if not info.get("State", {}).get("Running", False):
            await container.start()

        ws._client = client
        ws._container = container
        if ws_id:
            ws.workspace_id = ws_id

        info = await container.show()
        ports_info = info.get("NetworkSettings", {}).get("Ports", {})
        if gw_port:
            bindings = ports_info.get(f"{gw_port}/tcp", [])
            if bindings:
                ws._port_mapping[gw_port] = int(bindings[0]["HostPort"])

        if ws.mcp_servers:
            await ws._start_gateway()

        ws._started = True
        self._workspaces[ws.workspace_id] = ws
        logger.info(
            "DockerWorkspaceManager: restored workspace %s",
            ws.workspace_id,
        )
        return ws

    async def _create_for_pool(self) -> WorkspaceBase:
        return await self._do_create(
            user_id="__pool__",
            agent_id="__pool__",
            session_id="__pool__",
        )
