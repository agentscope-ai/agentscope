# -*- coding: utf-8 -*-
"""The base MCP stateful client class in AgentScope, that provides basic
 functionality for stateful MCP clients."""
import asyncio
from abc import ABC
from contextlib import AsyncExitStack
from typing import List

import mcp
from mcp import ClientSession

from ._client_base import MCPClientBase
from ._mcp_function import MCPToolFunction
from .._logging import logger


class StatefulClientBase(MCPClientBase, ABC):
    """The base class for stateful MCP clients in AgentScope, which maintains
    the session state across multiple tool calls.

    The developers should use `connect()` and `close()` methods to manage
    the client lifecycle.

    The context manager lifecycle (``AsyncExitStack`` enter/exit) is run
    inside a dedicated background task so that ``connect()`` and ``close()``
    can safely be called from different asyncio tasks — this avoids the
    ``anyio.CancelScope`` "exit in a different task" error that occurs in
    frameworks like uvicorn/FastAPI where startup and shutdown may run in
    separate tasks.
    """

    is_connected: bool
    """If connected to the MCP server"""

    def __init__(self, name: str) -> None:
        """Initialize the stateful MCP client.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
        """

        super().__init__(name=name)

        self.client = None
        self.stack = None
        self.session = None
        self.is_connected = False

        # Cache the tools to avoid fetching them multiple times
        self._cached_tools = None

        # Cross-task lifecycle management: a dedicated background task
        # owns the AsyncExitStack so that __aenter__/__aexit__ always
        # execute in the same task, satisfying anyio.CancelScope.
        self._lifecycle_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready_event: asyncio.Event | None = None
        self._init_error: BaseException | None = None

    # ------------------------------------------------------------------
    # Lifecycle worker
    # ------------------------------------------------------------------

    async def _lifecycle_worker(self) -> None:
        """Run the full context-manager lifecycle in one task.

        This method is spawned by ``connect()`` as a background task.
        It enters the ``AsyncExitStack``, signals readiness, then blocks
        until ``close()`` sets the stop event.  On exit the stack is
        closed **in the same task** that entered it, which is the key
        requirement of ``anyio.CancelScope``.
        """
        try:
            self.stack = AsyncExitStack()
            context = await self.stack.enter_async_context(
                self.client,
            )
            read_stream, write_stream = context[0], context[1]
            self.session = ClientSession(read_stream, write_stream)
            await self.stack.enter_async_context(self.session)
            await self.session.initialize()

            self.is_connected = True
            self._ready_event.set()
            logger.info("MCP client connected.")

            # Block until close() signals.  The wait may also be
            # interrupted by CancelledError if the session's internal
            # anyio cancel scope is torn down; treat that as a stop.
            try:
                await self._stop_event.wait()
            except (asyncio.CancelledError, Exception):
                pass

        except Exception as e:
            self._init_error = e
            self._ready_event.set()
        finally:
            self.session = None
            self.is_connected = False
            self._cached_tools = None
            if self.stack:
                try:
                    await self.stack.aclose()
                except Exception as e:
                    logger.warning(
                        "Error during MCP client cleanup: %s",
                        e,
                    )
                finally:
                    self.stack = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to MCP server.

        Spawns a background task that owns the full context-manager
        lifecycle so that ``close()`` can be called from any task.
        """
        if self.is_connected:
            raise RuntimeError(
                "The MCP server is already connected. Call close() "
                "before connecting again.",
            )

        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._init_error = None

        self._lifecycle_task = asyncio.create_task(
            self._lifecycle_worker(),
        )

        try:
            await self._ready_event.wait()
        except BaseException:
            # If connect() is cancelled externally (e.g. asyncio.wait_for
            # timeout), ensure the lifecycle worker is stopped.  We must
            # cancel the task (not just set _stop_event) because the worker
            # may still be blocked inside enter_async_context().
            self._lifecycle_task.cancel()
            try:
                await self._lifecycle_task
            except (asyncio.CancelledError, Exception):
                pass
            self._lifecycle_task = None
            raise

        if self._init_error is not None:
            await self._lifecycle_task
            self._lifecycle_task = None
            raise self._init_error

    async def close(self, ignore_errors: bool = True) -> None:
        """Clean up the MCP client resources. You must call this method when
        your application is done.

        Signals the background lifecycle task to exit and waits for full
        cleanup.

        Args:
            ignore_errors (`bool`):
                Whether to ignore errors during cleanup. Defaults to `True`.
        """
        if not self.is_connected and self._lifecycle_task is None:
            raise RuntimeError(
                "The MCP server is not connected. Call connect() before "
                "closing.",
            )

        try:
            if self._stop_event:
                self._stop_event.set()
            if self._lifecycle_task:
                await self._lifecycle_task
                self._lifecycle_task = None
        except Exception as e:
            if not ignore_errors:
                raise e
            logger.warning("Error during MCP client cleanup: %s", e)

    async def list_tools(self) -> List[mcp.types.Tool]:
        """Get all available tools from the server.

        Returns:
            `mcp.types.ListToolsResult`:
                A list of available MCP tools.
        """
        self._validate_connection()

        res = await self.session.list_tools()

        # Cache the tools for later use
        self._cached_tools = res.tools
        return res.tools

    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
        execution_timeout: float | None = None,
    ) -> MCPToolFunction:
        """Get an async tool function from the MCP server by its name, so
        that you can call it directly, wrap it into your own function, or
        anyway you like.

        .. note:: Currently, only the text, image, and audio results are
         supported in this function.

        Args:
            func_name (`str`):
                The name of the tool function to get.
            wrap_tool_result (`bool`):
                Whether to wrap the tool result into agentscope's
                `ToolResponse` object. If `False`, the raw result type
                `mcp.types.CallToolResult` will be returned.
            execution_timeout (`float | None`, optional):
                The preset timeout in seconds for calling the tool function.

        Returns:
            `MCPToolFunction`:
                A callable async function that returns either
                `mcp.types.CallToolResult` or `ToolResponse` when called.
        """
        self._validate_connection()

        if self._cached_tools is None:
            await self.list_tools()

        target_tool = None
        for tool in self._cached_tools:
            if tool.name == func_name:
                target_tool = tool
                break

        if target_tool is None:
            raise ValueError(
                f"Tool '{func_name}' not found in the MCP server",
            )

        return MCPToolFunction(
            mcp_name=self.name,
            tool=target_tool,
            wrap_tool_result=wrap_tool_result,
            session=self.session,
            timeout=execution_timeout,
        )

    def _validate_connection(self) -> None:
        """Validate the connection to the MCP server."""
        if not self.is_connected:
            raise RuntimeError(
                "The connection is not established. Call connect() "
                "before using the client.",
            )

        if not self.session:
            raise RuntimeError(
                "The session is not initialized. Call connect() "
                "before using the client.",
            )
