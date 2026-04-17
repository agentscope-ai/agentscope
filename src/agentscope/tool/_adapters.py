# -*- coding: utf-8 -*-
"""Adapters to convert functions and MCP tools to ToolProtocol."""
import inspect
from contextlib import _AsyncGeneratorContextManager
from datetime import timedelta
from typing import Callable, Any, AsyncGenerator

from mcp import ClientSession
import mcp

from ._types import Function
from ._base import ToolBase
from ._permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ._response import ToolChunk
from ._utils import _extract_func_description, _extract_input_schema
from .._logging import logger
from ..message import TextBlock, DataBlock, Base64Source, URLSource


class _FunctionTool(ToolBase):
    """Adapter to convert a Python function to ToolProtocol.

    This class wraps a regular Python function and makes it compatible with
    the ToolProtocol interface. It automatically extracts metadata from the
    function's signature and docstring, and normalizes the return value to
    AsyncGenerator[ToolChunk, None].
    """

    def __init__(
        self,
        func: Function,
        name: str | None = None,
        description: str | None = None,
        is_concurrency_safe: bool = True,
        is_read_only: bool = False,
    ):
        """Initialize the FunctionTool.

        Args:
            func (`Callable`):
                The Python function to wrap.
            name (`str | None`, optional):
                Custom tool name. If None, uses the function name.
            description (`str | None`, optional):
                Custom tool description. If None, extracts from docstring.
            is_concurrency_safe (`bool`, optional):
                Whether this tool is safe to call concurrently.
            is_read_only (`bool`, optional):
                Whether this tool only reads data without side effects.
        """
        self.name = name or func.__name__
        self.description = description or _extract_func_description(
            func.__doc__ or "",
        )
        self.input_schema = _extract_input_schema(func)
        self.is_concurrency_safe = is_concurrency_safe
        self.is_read_only = is_read_only
        self.is_mcp = False
        self._func = func

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool usage.

        Default implementation allows all operations.

        Returns:
            `PermissionDecision`:
                Permission decision (default: allowed).
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Custom function tools must be explicitly allowed "
            "by the user.",
        )

    async def __call__(
        self,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the wrapped function in an async style.

        Returns:
            `ToolChunk` or `AsyncGenerator[ToolChunk, None]`:
                The normalized result of the function execution.
        """
        if inspect.iscoroutinefunction(self._func):
            result = await self._func(**kwargs)
        else:
            result = self._func(**kwargs)

        return result


class MCPTool(ToolBase):
    """Adapter to convert an MCP tool to ToolProtocol.

    This class wraps an MCP tool and makes it compatible with the ToolProtocol
    interface. It handles the conversion between MCP's result format and
    AgentScope's ToolChunk format.
    """

    def __init__(
        self,
        mcp_name: str,
        tool: Any,
        client_gen: Callable[..., _AsyncGeneratorContextManager[Any]]
        | None = None,
        session: Any | None = None,
        timeout: float | None = None,
    ):
        """Initialize the MCPTool.

        Args:
            mcp_name (`str`):
                The name of the MCP server instance.
            tool (`mcp.types.Tool`):
                The MCP tool definition.
            client_gen (`Callable[..., _AsyncGeneratorContextManager[Any]] \
            | None`, optional):
                The MCP client generator function for stateless clients.
                Either this or ``session`` must be provided.
            session (`mcp.ClientSession | None`, optional):
                The MCP client session for stateful clients.
                Either this or ``client_gen`` must be provided.
            timeout (`float | None`, optional):
                The timeout in seconds for tool execution.
        """
        self.mcp_name = mcp_name
        self.name = tool.name
        self.description = tool.description or ""

        # Extract input schema
        self.input_schema = {
            "type": "object",
            "properties": tool.inputSchema.get("properties", {}),
            "required": tool.inputSchema.get("required", []),
        }

        self.is_concurrency_safe = True

        # Extract is_read_only from MCP tool annotations
        self.is_read_only = False
        if tool.annotations and hasattr(tool.annotations, "readOnlyHint"):
            self.is_read_only = tool.annotations.readOnlyHint or False

        self.is_mcp = True

        # Store MCP tool and connection info
        self._tool = tool
        self._client_gen = client_gen
        self._session = session

        if timeout:
            self._timeout = timedelta(seconds=timeout)
        else:
            self._timeout = None

        # Validate that either client_gen or session is provided
        if (client_gen is None and session is None) or (
            client_gen is not None and session is not None
        ):
            raise ValueError(
                "Either client_gen or session must be provided, but not both.",
            )

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the MCP tool usage.

        Default implementation allows all operations.

        Returns:
            `PermissionDecision`:
                Permission decision (default: ask for confirmation).
        """
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="This is a read-only MCP tool. Allowing execution.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="xxx",
        )

    async def __call__(
        self,
        **kwargs: Any,
    ) -> ToolChunk:
        """Invoke the MCP tool and convert the result to ToolChunk.

        Args:
            **kwargs: Arguments to pass to the MCP tool.

        Returns:
            `ToolChunk`: The converted tool execution result.
        """

        # Call the MCP tool
        if self._client_gen:
            # Stateless client: create temporary session
            async with self._client_gen() as cli:
                read_stream, write_stream = cli[0], cli[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        self.name,
                        arguments=kwargs,
                        read_timeout_seconds=self._timeout,
                    )
        else:
            # Stateful client: use existing session
            result = await self._session.call_tool(
                self.name,
                arguments=kwargs,
                read_timeout_seconds=self._timeout,
            )

        # Convert MCP result to AgentScope blocks
        return ToolChunk(
            content=self._convert_mcp_content_to_blocks(result.content),
            state="error" if result.isError else "running",
        )

    @staticmethod
    def _convert_mcp_content_to_blocks(
        mcp_content_blocks: list,
    ) -> list[TextBlock | DataBlock]:
        """Convert MCP content to AgentScope blocks.

        Args:
            mcp_content_blocks (`list`):
                The MCP content blocks to convert.

        Returns:
            `list[TextBlock | DataBlock]`: Converted AgentScope blocks.
        """

        as_content = []
        for content in mcp_content_blocks:
            if isinstance(content, mcp.types.TextContent):
                as_content.append(TextBlock(text=content.text))
            elif isinstance(
                content,
                (mcp.types.ImageContent, mcp.types.AudioContent),
            ):
                as_content.append(
                    DataBlock(
                        source=Base64Source(
                            type="base64",
                            media_type=content.mimeType,
                            data=content.data,
                        ),
                    ),
                )

            elif isinstance(content, mcp.types.EmbeddedResource):
                if isinstance(
                    content.resource,
                    mcp.types.TextResourceContents,
                ):
                    as_content.append(
                        TextBlock(
                            text=content.resource.model_dump_json(indent=2),
                        ),
                    )
                else:
                    logger.error(
                        "Unsupported EmbeddedResource content type: %s. "
                        "Skipping this content.",
                        type(content.resource),
                    )

            elif isinstance(content, mcp.types.ResourceContents):
                as_content.append(
                    DataBlock(
                        source=URLSource(
                            media_type=content.mimeType,
                            url=content.uri,
                        ),
                    ),
                )

            else:
                logger.warning(
                    "Unsupported content type: %s. Skipping this content.",
                    type(content),
                )
        return as_content
