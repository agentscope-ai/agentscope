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

    The context manager lifecycle (enter/exit of MCP transports) runs in a
    single dedicated asyncio task to avoid cross-task CancelScope errors
    from anyio, which is used internally by MCP transports (stdio_client,
    sse_client, etc.). This prevents resource leaks when ``connect()`` and
    ``close()`` are called from different asyncio tasks (e.g. in
    uvicorn/FastAPI startup vs. shutdown events).
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

        self._lifecycle_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._connect_error: BaseException | None = None

    async def _run_lifecycle(self) -> None:
        """Run the MCP client lifecycle in a dedicated task.

        Ensures ``AsyncExitStack.__aenter__`` and ``__aexit__`` happen in
        the same asyncio task, avoiding cross-task ``CancelScope`` errors.
        """
        try:
            async with AsyncExitStack() as stack:
                self.stack = stack

                client = self.client
                if client is None:
                    raise RuntimeError(
                        "client is not set. Subclasses must assign "
                        "self.client in __init__.",
                    )
                context = await stack.enter_async_context(client)
                read_stream, write_stream = context[0], context[1]

                session = ClientSession(read_stream, write_stream)
                await stack.enter_async_context(session)
                await session.initialize()
                self.session = session

                self.is_connected = True
                self._ready_event.set()
                logger.info("MCP client connected.")

                await self._stop_event.wait()

        except Exception as e:
            if not self._ready_event.is_set():
                self._connect_error = e
                self._ready_event.set()
            else:
                logger.warning("Error in MCP client lifecycle: %s", e)
        except BaseException:
            # CancelledError (and other BaseException subclasses like
            # SystemExit) are not caught by `except Exception`. Ensure
            # _ready_event is always set so connect() can unblock instead
            # of hanging forever.
            if not self._ready_event.is_set():
                self._ready_event.set()
            raise
        finally:
            self.stack = None
            self.session = None
            self.is_connected = False
            self._cached_tools = None

    async def connect(self) -> None:
        """Connect to MCP server.

        Spawns a background task that owns the full context-manager
        lifecycle so that ``close()`` can be called from any task.
        """
        if self.is_connected or (
            self._lifecycle_task is not None
            and not self._lifecycle_task.done()
        ):
            raise RuntimeError(
                "The MCP server is already connected. Call close() "
                "before connecting again.",
            )

        self._stop_event.clear()
        self._ready_event.clear()
        self._connect_error = None
        self._lifecycle_task = asyncio.create_task(self._run_lifecycle())

        try:
            await self._ready_event.wait()
        except BaseException:
            self._stop_event.set()
            if (
                self._lifecycle_task is not None
                and not self._lifecycle_task.done()
            ):
                self._lifecycle_task.cancel()
                try:
                    await self._lifecycle_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._lifecycle_task = None
            raise

        if self._connect_error is not None:
            error = self._connect_error
            tb = error.__traceback__
            self._connect_error = None
            if self._lifecycle_task and not self._lifecycle_task.done():
                await self._lifecycle_task
            self._lifecycle_task = None
            raise error.with_traceback(tb)

        if not self.is_connected or self._stop_event.is_set():
            if self._lifecycle_task and not self._lifecycle_task.done():
                await self._lifecycle_task
            self._lifecycle_task = None
            raise RuntimeError(
                "The MCP server was closed during connect().",
            )

    async def close(self, ignore_errors: bool = True) -> None:
        """Clean up the MCP client resources. You must call this method when
        your application is done.

        This method is safe to call from any asyncio task — it signals the
        dedicated lifecycle task to exit, which performs the actual context
        manager cleanup in the same task that entered it.

        Args:
            ignore_errors (`bool`):
                Whether to ignore errors during cleanup. Defaults to `True`.
        """
        has_lifecycle = (
            self._lifecycle_task is not None
            and not self._lifecycle_task.done()
        )

        if not self.is_connected and not has_lifecycle:
            if not ignore_errors:
                raise RuntimeError(
                    "The MCP server is not connected. Call connect() before "
                    "closing.",
                )
            return

        try:
            self._stop_event.set()
            if self._lifecycle_task:
                await self._lifecycle_task
        except Exception as e:
            if not ignore_errors:
                raise
            logger.warning("Error during MCP client cleanup: %s", e)
        finally:
            self._lifecycle_task = None

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
