# -*- coding: utf-8 -*-
"""MCP client manager for connection pooling and lifecycle management."""
import asyncio
from typing import Optional

from ...mcp import MCPClient
from .._schema import ConnectionScope
from ...storage.models import MCPModel
from ..._logging import logger


class MCPClientManager:
    """Manager for MCP client connections and lifecycle.

    This manager handles connection pooling and lifecycle management for
    MCP clients. It should be initialized as part of the FastAPI lifespan
    and cleaned up on application shutdown.

    Connection pooling strategy (based on ConnectionScope):
    - SHARED: One connection per MCP config, shared across all agents
      Pool key: (mcp_name,)
    - ISOLATED: One connection per (MCP config, agent), isolated per agent
      Pool key: (mcp_name, agent_id)
    - EPHEMERAL: No pooling, create new connection on each call
      Pool key: N/A (not stored in pool)

    Example:
        ```python
        from contextlib import asynccontextmanager
        from fastapi import FastAPI, Depends, Request

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup: Initialize client manager
            client_manager = MCPClientManager()
            app.state.mcp_client_manager = client_manager

            yield

            # Shutdown: Clean up all connections
            await client_manager.close_all()

        app = FastAPI(lifespan=lifespan)

        # Dependency injection
        def get_mcp_manager(request: Request) -> MCPClientManager:
            return request.app.state.mcp_client_manager

        @app.post("/chat")
        async def chat(
            agent_id: str,
            mcp_config: MCPModel,  # From database
            mcp_manager: MCPClientManager = Depends(get_mcp_manager)
        ):
            # Get or create MCP client for this agent
            client = await mcp_manager.get_client(mcp_config, agent_id)
            # Use client...
        ```
    """

    def __init__(self) -> None:
        """Initialize the MCP client manager."""
        # Connection pool: key = (mcp_name,) or (mcp_name, agent_id)
        self._pool: dict[tuple, MCPClient] = {}

        # Locks for preventing concurrent creation of the same connection
        # This ensures only one connection is created per pool key
        self._locks: dict[tuple, asyncio.Lock] = {}

    async def get_client(
        self,
        mcp_config: MCPServiceConfig,
        agent_id: Optional[str] = None,
    ) -> MCPClient:
        """Get or create an MCP client connection.

        This method implements lazy loading: connections are created on first
        use and reused for subsequent calls (except for EPHEMERAL scope).

        Args:
            mcp_config: The MCP service configuration (includes connection_scope).
            agent_id: The agent ID (required for ISOLATED scope).

        Returns:
            The MCP client instance.

        Raises:
            ValueError: If agent_id is required but not provided, or if
                the configuration is invalid.

        Example:
            ```python
            # Shared connection (all agents use the same client)
            config = MCPServiceConfig(
                connection_scope=ConnectionScope.SHARED,
                mcp_config=HttpMCPConfig(...)
            )
            client = await manager.get_client(config)

            # Isolated connection (each agent has its own client)
            config = MCPServiceConfig(
                connection_scope=ConnectionScope.ISOLATED,
                mcp_config=StdioMCPConfig(...)
            )
            client = await manager.get_client(config, agent_id="user123")

            # Ephemeral connection (new client on each call)
            config = MCPServiceConfig(
                connection_scope=ConnectionScope.EPHEMERAL,
                mcp_config=HttpMCPConfig(...)
            )
            client = await manager.get_client(config)
            ```
        """
        # Validate configuration
        mcp_config.validate_config()

        mcp_name = mcp_config.name

        # For EPHEMERAL scope, create a new client each time (no pooling)
        if mcp_config.connection_scope == ConnectionScope.EPHEMERAL:
            logger.debug(
                f"Creating ephemeral MCP client for '{mcp_name}'",
            )
            return self._create_client(mcp_config)

        # Determine the pool key based on connection scope
        if mcp_config.connection_scope == ConnectionScope.SHARED:
            pool_key = (mcp_name,)
        else:  # ISOLATED
            if agent_id is None:
                raise ValueError(
                    f"agent_id is required for ISOLATED connection scope "
                    f"(MCP: '{mcp_name}')",
                )
            pool_key = (mcp_name, agent_id)

        # Check if connection already exists in pool
        if pool_key in self._pool:
            logger.debug(
                f"Reusing existing MCP client from pool: {pool_key}",
            )
            return self._pool[pool_key]

        # Connection doesn't exist, need to create it
        # Use lock to prevent concurrent creation (double-checked locking)
        lock = self._locks.setdefault(pool_key, asyncio.Lock())

        async with lock:
            # Double-check: another coroutine might have created it
            # while we were waiting for the lock
            if pool_key in self._pool:
                logger.debug(
                    f"Connection created by another coroutine: {pool_key}",
                )
                return self._pool[pool_key]

            # Create the connection
            logger.info(
                f"Creating new MCP client for pool key: {pool_key}",
            )
            client = self._create_client(mcp_config)

            # For stateful connections, connect immediately
            if client.is_stateful:
                await client.connect()

            self._pool[pool_key] = client

            return client

    def _create_client(
        self,
        mcp_config: MCPServiceConfig,
    ) -> MCPClient:
        """Create an MCP client from service config.

        Args:
            mcp_config: The MCP service configuration.

        Returns:
            The created MCP client instance (not connected yet).
        """
        # Determine if this should be stateful
        # - STDIO: always stateful
        # - HTTP with EPHEMERAL: stateless
        # - HTTP with SHARED/ISOLATED: stateful
        is_stateful = (
            mcp_config.mcp_config.type == "stdio_mcp"
            or mcp_config.connection_scope != ConnectionScope.EPHEMERAL
        )

        return MCPClient(
            name=mcp_config.name,
            is_stateful=is_stateful,
            mcp_config=mcp_config.mcp_config,
        )

    async def close_client(
        self,
        mcp_name: str,
        agent_id: Optional[str] = None,
        connection_scope: ConnectionScope = ConnectionScope.ISOLATED,
    ) -> bool:
        """Close and remove a specific MCP client from the pool.

        This is useful for cleaning up agent-specific connections when
        an agent session ends.

        Args:
            mcp_name: The name of the MCP configuration.
            agent_id: The agent ID (for ISOLATED scope).
            connection_scope: The connection scope to determine pool key.

        Returns:
            True if the client was found and closed, False otherwise.
        """
        # Determine the pool key
        if connection_scope == ConnectionScope.SHARED:
            pool_key = (mcp_name,)
        elif connection_scope == ConnectionScope.ISOLATED:
            if agent_id is None:
                return False
            pool_key = (mcp_name, agent_id)
        else:  # EPHEMERAL
            # Ephemeral clients are not pooled
            return False

        # Remove from pool and close
        client = self._pool.pop(pool_key, None)
        if client is not None:
            logger.info(f"Closing MCP client: {pool_key}")
            await client.close()
            return True

        return False

    async def close_all(self) -> None:
        """Close all MCP connections in the pool.

        This should be called during application shutdown to clean up
        all resources.
        """
        logger.info(
            f"Closing all MCP connections ({len(self._pool)} clients)",
        )

        for pool_key, client in self._pool.items():
            try:
                logger.debug(f"Closing MCP client: {pool_key}")
                await client.close()
            except Exception as e:
                logger.error(
                    f"Error closing MCP client {pool_key}: {e}",
                    exc_info=True,
                )

        self._pool.clear()
        self._locks.clear()

        logger.info("All MCP connections closed")
