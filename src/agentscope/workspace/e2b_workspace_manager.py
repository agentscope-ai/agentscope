# -*- coding: utf-8 -*-
"""E2BWorkspaceManager — manages :class:`E2BWorkspace` instances.

Creates E2B cloud-sandbox workspaces with shared default configuration.

Usage::

    manager = E2BWorkspaceManager(
        template="my-template",
        api_key="e2b-api-key",
    )
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
from .e2b_workspace import E2BWorkspace
from .types import SerializedWorkspaceState
from .workspace_base import WorkspaceBase
from .workspace_manager_base import WorkspaceManagerBase


class E2BWorkspaceManager(WorkspaceManagerBase):
    """Manages E2B cloud-sandbox workspaces."""

    def __init__(
        self,
        template: str = E2BWorkspace.DEFAULT_TEMPLATE,
        api_key: str = "",
        domain: str = "",
        timeout_seconds: int = E2BWorkspace.DEFAULT_TIMEOUT,
        working_dir: str = E2BWorkspace.DEFAULT_WORKING_DIR,
        default_mcp_servers: list[MCPClient] | None = None,
        gateway_port: int = E2BWorkspace.GATEWAY_PORT,
        default_env: dict[str, str] | None = None,
        default_metadata: dict[str, str] | None = None,
        default_startup_commands: list[str] | None = None,
    ) -> None:
        """Create an E2B workspace manager.

        Args:
            template: Default E2B sandbox template.
            api_key: E2B API key (shared across all workspaces).
            domain: Optional E2B API domain override.
            timeout_seconds: Default sandbox auto-shutdown timeout.
            working_dir: Default working directory inside sandboxes.
            default_mcp_servers: MCP clients configured in every
                new workspace.
            gateway_port: Default gateway port for new workspaces.
            default_env: Environment variables set in every new
                sandbox.
            default_metadata: Metadata attached to every new
                sandbox.
            default_startup_commands: Shell commands run in every
                new sandbox after creation.
        """
        super().__init__()
        self._template = template
        self._api_key = api_key
        self._domain = domain
        self._timeout_seconds = timeout_seconds
        self._working_dir = working_dir
        self._default_mcp_servers = list(default_mcp_servers or [])
        self._gateway_port = gateway_port
        self._default_env = dict(default_env or {})
        self._default_metadata = dict(default_metadata or {})
        self._default_startup_commands = list(
            default_startup_commands or [],
        )

    async def initialize(self) -> None:
        """Log readiness (E2B API client is created lazily)."""
        logger.info(
            "E2BWorkspaceManager: initialized (template=%s)",
            self._template,
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
        """Create and initialise a new :class:`E2BWorkspace`."""
        ws = E2BWorkspace(
            template=kwargs.get("template", self._template),
            api_key=kwargs.get("api_key", self._api_key),
            domain=kwargs.get("domain", self._domain),
            timeout_seconds=kwargs.get(
                "timeout",
                self._timeout_seconds,
            ),
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
            metadata=dict(
                kwargs.get("metadata", self._default_metadata),
            ),
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

        connect_kwargs: dict[str, Any] = {
            "sandbox_id": sandbox_id,
        }
        if self._api_key:
            connect_kwargs["api_key"] = self._api_key
        if self._domain:
            connect_kwargs["domain"] = self._domain

        sandbox = await AsyncSandbox.connect(**connect_kwargs)

        ws = E2BWorkspace(
            api_key=self._api_key,
            domain=self._domain,
            working_dir=working_dir,
            mcp_servers=list(self._default_mcp_servers),
            gateway_port=self._gateway_port,
        )
        ws._sandbox = sandbox
        ws_id = state.payload.get("workspace_id")
        if ws_id:
            ws.workspace_id = ws_id

        if ws.mcp_servers:
            await ws._start_gateway()

        ws._started = True
        self._workspaces[ws.workspace_id] = ws
        logger.info(
            "E2BWorkspaceManager: restored workspace %s",
            ws.workspace_id,
        )
        return ws

    async def _create_for_pool(self) -> WorkspaceBase:
        return await self._do_create(
            user_id="__pool__",
            agent_id="__pool__",
            session_id="__pool__",
        )
