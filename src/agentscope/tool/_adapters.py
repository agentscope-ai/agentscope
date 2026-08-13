# -*- coding: utf-8 -*-
"""Adapters to convert functions and MCP tools to ToolProtocol."""
import inspect
import ipaddress
import json
import re
import socket
from contextlib import _AsyncGeneratorContextManager
from datetime import timedelta
from typing import Callable, Any, AsyncGenerator, Generator
from urllib.parse import urlparse

from mcp import ClientSession
import mcp

from ._types import Function
from ._base import ToolBase, ToolMiddlewareBase
from ..permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ._response import ToolChunk
from ._utils import _extract_func_description, _extract_input_schema
from .._logging import logger
from ..message import (
    TextBlock,
    DataBlock,
    Base64Source,
    URLSource,
    ToolResultState,
)


# Security: Tool name patterns that indicate destructive operations.
# These should never be treated as read-only, even if the MCP server
# reports readOnlyHint=True.
_DESTRUCTIVE_TOOL_PATTERNS = re.compile(
    r"(delete|remove|destroy|drop|truncate|wipe|erase|exec|eval|"
    r"run|shell|bash|cmd|write|create|update|insert|modify|"
    r"rename|move|copy|upload|deploy|install|uninstall|"
    r"send|post|put|patch|kill|stop|restart|format|"
    r"mkdir|rmdir|chmod|chown|unlink|append|set|add|"
    r"reset|clear|purge|flush|revoke|ban|block)",
    re.IGNORECASE,
)

# Security: Maximum length for text content from MCP servers
# to prevent memory exhaustion (1M characters).
_MAX_TEXT_CONTENT_LENGTH = 1_000_000

# Security: Maximum size for base64-encoded content from MCP servers
# to prevent memory exhaustion (10 MB).
_MAX_BASE64_CONTENT_SIZE = 10 * 1024 * 1024

# Security: Cloud metadata endpoints that must be blocked in URLs.
_CLOUD_METADATA_HOSTS = frozenset({
    "169.254.169.254",       # AWS / GCP / Azure / Oracle metadata
    "fd00:ec2::254",         # AWS IPv6 metadata
    "metadata.google.internal",
    "metadata.goog",
})


def _validate_content_url(url: str) -> str:
    """Validate a URL found in MCP response content.

    Checks that the URL does not target private IP ranges, loopback
    addresses, link-local addresses, or cloud metadata endpoints.

    Args:
        url: The URL to validate.

    Returns:
        The validated URL string.

    Raises:
        ValueError: If the URL targets a disallowed network resource.
    """
    try:
        parsed = urlparse(str(url))
    except Exception as e:
        raise ValueError(f"Invalid URL in MCP content: {e}") from e

    # Only allow http and https schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme '{parsed.scheme}' in MCP content is not allowed. "
            "Only 'http' and 'https' are permitted.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL in MCP content does not contain a hostname.")

    # Block cloud metadata endpoints
    if hostname.lower() in _CLOUD_METADATA_HOSTS:
        raise ValueError(
            "URL in MCP content targets a cloud metadata endpoint, "
            "which is blocked for security reasons.",
        )

    # Resolve hostname and check for private/reserved ranges
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # If DNS resolution fails, allow the URL — it won't resolve
        # at connection time either.
        return str(url)

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(
                f"URL in MCP content resolves to {ip}, which is in a "
                "private or reserved IP range. Blocked for SSRF protection.",
            )

    return str(url)


def _is_tool_likely_destructive(tool_name: str) -> bool:
    """Heuristic check whether a tool name suggests destructive operations.

    Args:
        tool_name: The name of the MCP tool.

    Returns:
        True if the tool name matches known destructive patterns.
    """
    return bool(_DESTRUCTIVE_TOOL_PATTERNS.search(tool_name))


class FunctionTool(ToolBase):
    """Adapter to convert a Python function to ToolProtocol.

    This class wraps a regular Python function and makes it compatible with
    the ToolProtocol interface. It automatically extracts metadata from the
    function's signature and docstring, and normalizes the return value to
    ToolChunk or AsyncGenerator[ToolChunk, None].
    """

    is_external_tool: bool = False
    """If this tool is an external tool, which doesn't need to implement the
    __call__ method and the agent will yield the external tool call event."""
    is_mcp: bool = False
    """If this tool is an MCP tool, which will be used in the permission"""
    mcp_name: str | None = None
    """The name of the MCP server this tool belongs to, which is required if
    this tool is an MCP tool."""

    def __init__(
        self,
        func: Function,
        name: str | None = None,
        description: str | None = None,
        is_concurrency_safe: bool = True,
        is_read_only: bool = False,
        is_state_injected: bool = False,
        middlewares: list[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the FunctionTool.

        Args:
            func (`Callable`):
                The Python function to wrap.
            name (`str | None`, optional):
                Custom tool name. If None, uses the function name.
            description (`str | None`, optional):
                Custom description. If None, extracts from docstring.
            is_concurrency_safe (`bool`, optional):
                Whether this tool is safe to call concurrently.
            is_read_only (`bool`, optional):
                Whether this tool only reads data without side effects.
            is_state_injected (`bool`, optional):
                Whether this tool requires agent state injection.
            middlewares (`list[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        self.name = name or func.__name__
        self.description = description or _extract_func_description(
            func.__doc__ or "",
        )
        self.input_schema = _extract_input_schema(func)
        self.is_concurrency_safe = is_concurrency_safe
        self.is_read_only = is_read_only
        self.is_state_injected = is_state_injected
        self.is_external_tool = False
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

    async def call(
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

        if isinstance(result, AsyncGenerator):

            async def _stream() -> AsyncGenerator[ToolChunk, None]:
                async for chunk in result:
                    if isinstance(chunk, ToolChunk):
                        yield chunk
                    else:
                        yield self._convert_func_result_to_chunk(chunk)

            return _stream()

        if isinstance(result, Generator):

            async def _stream() -> AsyncGenerator[ToolChunk, None]:
                for chunk in result:
                    if isinstance(chunk, ToolChunk):
                        yield chunk
                    else:
                        yield self._convert_func_result_to_chunk(chunk)

            return _stream()

        return self._convert_func_result_to_chunk(result)

    @staticmethod
    def _convert_func_result_to_chunk(
        result: Any,
    ) -> ToolChunk:
        if isinstance(result, ToolChunk):
            return result
        if isinstance(result, str):
            text = result
        else:
            try:
                text = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(result)
        return ToolChunk(
            content=[TextBlock(text=text)],
            state=ToolResultState.RUNNING,
        )


class MCPTool(ToolBase):
    """Adapter to convert an MCP tool to ToolProtocol.

    This class wraps an MCP tool and makes it compatible with the ToolProtocol
    interface. It handles the conversion between MCP's result format and
    AgentScope's ToolChunk format.
    """

    is_mcp: bool = True
    """Whether this tool is an MCP tool."""
    is_state_injected: bool = False
    """The mcp tools is prohibited state injection for safety reason."""

    def __init__(
        self,
        mcp_name: str,
        tool: mcp.types.Tool,
        client_gen: Callable[..., _AsyncGeneratorContextManager[Any]]
        | None = None,
        session: Any | None = None,
        timeout: float | None = None,
        middlewares: list[ToolMiddlewareBase] | None = None,
    ) -> None:
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
            middlewares (`list[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        self.mcp_name = mcp_name

        # LLM providers enforce ^[a-zA-Z0-9_-]+$ on tool names.
        # mcp_name is validated in MCPClient.model_post_init;
        # tool.name comes from the MCP server and may contain dots,
        # colons, etc. — replace illegal chars with "x" (not "_")
        # to avoid collisions with the "__" separator.
        sanitized_tool = re.sub(r"[^a-zA-Z0-9_-]", "x", tool.name)
        self.name = f"mcp__{mcp_name}__{sanitized_tool}"
        if sanitized_tool != tool.name:
            logger.debug(
                "MCP tool name sanitized: '%s' -> '%s'.",
                tool.name,
                self.name,
            )

        self.description = tool.description or ""

        # Preserve the full inputSchema (including $defs, anyOf, oneOf, etc.)
        # rather than only copying "properties" and "required", which would
        # silently drop any nested type definitions that the LLM needs to
        # resolve $ref pointers.
        _schema = dict(tool.inputSchema) if tool.inputSchema else {}
        _schema.setdefault("type", "object")
        _schema.setdefault("properties", {})
        _schema.setdefault("required", [])
        self.input_schema = _schema

        # By default
        self.is_concurrency_safe = False
        self.is_external_tool = False

        # Security: Extract is_read_only from MCP tool annotations, but
        # apply a heuristic check to prevent trust boundary bypass.
        # A malicious MCP server could mark destructive tools as read-only.
        self.is_read_only = False
        if tool.annotations and hasattr(tool.annotations, "readOnlyHint"):
            server_read_only = tool.annotations.readOnlyHint or False
            if server_read_only and _is_tool_likely_destructive(tool.name):
                logger.warning(
                    "MCP tool '%s' claims readOnlyHint=True but its name "
                    "suggests destructive operations. Overriding to "
                    "read_only=False for security.",
                    tool.name,
                )
                self.is_read_only = False
            else:
                self.is_read_only = server_read_only

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
            message="MCP tools must be explicitly allowed by the user.",
        )

    async def call(
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
                        self._tool.name,
                        arguments=kwargs,
                        read_timeout_seconds=self._timeout,
                    )
        else:
            # Stateful client: use existing session
            result = await self._session.call_tool(
                self._tool.name,
                arguments=kwargs,
                read_timeout_seconds=self._timeout,
            )

        # Convert MCP result to AgentScope blocks
        return ToolChunk(
            content=self._convert_mcp_content_to_blocks(result.content),
            state=ToolResultState.ERROR
            if result.isError
            else ToolResultState.RUNNING,
        )

    @staticmethod
    def _convert_mcp_content_to_blocks(
        mcp_content_blocks: list,
    ) -> list[TextBlock | DataBlock]:
        """Convert MCP content to AgentScope blocks.

        Security: Text content is length-limited and URLs in resource
        content are validated against SSRF attacks.

        Args:
            mcp_content_blocks (`list`):
                The MCP content blocks to convert.

        Returns:
            `list[TextBlock | DataBlock]`: Converted AgentScope blocks.
        """

        as_content = []
        for content in mcp_content_blocks:
            if isinstance(content, mcp.types.TextContent):
                # Security: Enforce text content length limit to prevent
                # memory exhaustion attacks
                text = content.text
                if len(text) > _MAX_TEXT_CONTENT_LENGTH:
                    logger.warning(
                        "MCP text content truncated from %d to %d "
                        "characters for security.",
                        len(text),
                        _MAX_TEXT_CONTENT_LENGTH,
                    )
                    text = text[:_MAX_TEXT_CONTENT_LENGTH]
                as_content.append(TextBlock(text=text))
            elif isinstance(
                content,
                (mcp.types.ImageContent, mcp.types.AudioContent),
            ):
                # Security: Enforce base64 content size limit to prevent
                # memory exhaustion attacks
                data = content.data
                if len(data) > _MAX_BASE64_CONTENT_SIZE:
                    logger.warning(
                        "MCP %s content (%d bytes) exceeds maximum "
                        "allowed size (%d bytes). Skipping for security.",
                        "image" if isinstance(
                            content, mcp.types.ImageContent,
                        ) else "audio",
                        len(data),
                        _MAX_BASE64_CONTENT_SIZE,
                    )
                    continue
                as_content.append(
                    DataBlock(
                        source=Base64Source(
                            type="base64",
                            media_type=content.mimeType,
                            data=data,
                        ),
                    ),
                )

            elif isinstance(content, mcp.types.EmbeddedResource):
                if isinstance(
                    content.resource,
                    mcp.types.TextResourceContents,
                ):
                    resource_text = content.resource.model_dump_json(indent=2)
                    # Security: Enforce text content length limit
                    if len(resource_text) > _MAX_TEXT_CONTENT_LENGTH:
                        logger.warning(
                            "MCP embedded resource text truncated from "
                            "%d to %d characters for security.",
                            len(resource_text),
                            _MAX_TEXT_CONTENT_LENGTH,
                        )
                        resource_text = resource_text[:_MAX_TEXT_CONTENT_LENGTH]
                    as_content.append(
                        TextBlock(text=resource_text),
                    )
                else:
                    logger.error(
                        "Unsupported EmbeddedResource content type: %s. "
                        "Skipping this content.",
                        type(content.resource),
                    )

            elif isinstance(content, mcp.types.ResourceContents):
                # Security: Validate URL in resource content against SSRF
                try:
                    validated_url = _validate_content_url(content.uri)
                    as_content.append(
                        DataBlock(
                            source=URLSource(
                                media_type=content.mimeType,
                                url=validated_url,
                            ),
                        ),
                    )
                except ValueError as e:
                    logger.warning(
                        "Blocked URL in MCP resource content for "
                        "security: %s",
                        str(e),
                    )

            else:
                logger.warning(
                    "Unsupported content type: %s. Skipping this content.",
                    type(content),
                )
        return as_content
