# -*- coding: utf-8 -*-
"""Adapters to convert functions and MCP tools to ToolProtocol."""
import asyncio
import hashlib
import inspect
import json
import re
import warnings
from collections import defaultdict
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Callable, Any, AsyncGenerator, Generator

import anyio
import mcp
from pydantic import AnyUrl, BaseModel, ValidationError

from ._types import Function
from ._base import ToolBase, ToolMiddlewareBase
from ..permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ._response import ToolChunk
from ._utils import (
    _extract_func_description,
    _extract_input_schema,
    _get_callable_docstring,
    _get_callable_name,
    _remove_title_field,
)
from .._logging import logger
from ..message import (
    Base64Source,
    TextBlock,
    DataBlock,
    ToolResultState,
    URLSource,
)

# OpenAI and Anthropic reject a function name longer than 64 characters,
# and one over-long name fails the whole model request (#3371). Collision
# suffixes stay inside this budget so a later length-limit pass does not
# have to shorten two already-distinct names back into each other.
_MAX_TOOL_NAME_LENGTH = 64
_MCP_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")
_DIGEST_LENGTHS = (8, 16, 32, 48, 63)


def _mcp_raw_identity(tool: ToolBase) -> tuple[str, str] | None:
    """Return ``(server, raw tool name)`` for an MCP tool.

    The raw name is the one the MCP server is called with. Other tools,
    and MCP adapters that do not keep the original descriptor, return
    ``None``.
    """
    if not getattr(tool, "is_mcp", False):
        return None
    raw_tool = getattr(tool, "_tool", None)
    raw_name = getattr(raw_tool, "name", None)
    mcp_name = getattr(tool, "mcp_name", None)
    if not isinstance(raw_name, str) or not isinstance(mcp_name, str):
        return None
    return mcp_name, raw_name


def _owns_unsanitized_base(
    base_name: str,
    mcp_name: str,
    raw_name: str,
) -> bool:
    """Whether *raw_name* is already legal and composes to *base_name*.

    That identity is the one that can keep the unsuffixed spelling. Two
    different raw names cannot both own it: the composed string contains
    the raw name itself.
    """
    if _MCP_NAME_PATTERN.search(raw_name):
        return False
    return f"mcp__{mcp_name}__{raw_name}" == base_name


def _fit_provider_name(base_name: str, suffix: str) -> str:
    """Append *suffix*, staying within the 64-character provider limit."""
    if len(suffix) > _MAX_TOOL_NAME_LENGTH:
        suffix = suffix[-_MAX_TOOL_NAME_LENGTH:]
    if len(base_name) + len(suffix) <= _MAX_TOOL_NAME_LENGTH:
        return base_name + suffix
    kept = _MAX_TOOL_NAME_LENGTH - len(suffix)
    if kept <= 0:
        return suffix[-_MAX_TOOL_NAME_LENGTH:]
    return base_name[:kept] + suffix


def _identity_digest(mcp_name: str, raw_name: str) -> str:
    """Hash the unsanitized ``server + raw tool name`` identity."""
    payload = f"{mcp_name}\0{raw_name}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _blocked_name(
    candidate: str,
    identity: tuple[str, str],
    holders: dict[str, set[tuple[str, str] | None]],
) -> bool:
    """Whether *candidate* is already used by some other identity."""
    return any(holder != identity for holder in holders.get(candidate, ()))


def _allocate_disambiguated_name(
    base_name: str,
    identity: tuple[str, str],
    holders: dict[str, set[tuple[str, str] | None]],
) -> str:
    """Pick a suffix of the original identity that nobody else is using.

    Raises:
        ValueError: If every digest length still collides with a name
            held by a different tool.
    """
    digest = _identity_digest(identity[0], identity[1])
    for length in _DIGEST_LENGTHS:
        candidate = _fit_provider_name(base_name, f"_{digest[:length]}")
        if _MCP_NAME_PATTERN.search(candidate):
            continue
        if not _blocked_name(candidate, identity, holders):
            return candidate
    raise ValueError(
        f"Cannot give MCP tool '{identity[1]}' on server '{identity[0]}' "
        f"a model-facing name distinct from '{base_name}'. Two different "
        "tools would otherwise be exposed as one.",
    )


def _canonical_mcp_name(mcp_name: str, raw_name: str) -> str:
    """Model-facing name ``MCPTool`` assigns before disambiguation."""
    sanitized = _MCP_NAME_PATTERN.sub("x", raw_name)
    return f"mcp__{mcp_name}__{sanitized}"


def disambiguate_sanitized_mcp_names(tools: list[ToolBase]) -> None:
    """Give sanitization-colliding MCP tools distinct model-facing names.

    ``MCPTool`` replaces characters outside ``[a-zA-Z0-9_-]`` with ``x``.
    Distinct server tools such as ``search.docs`` and ``searchxdocs`` then
    share one model-facing name, and toolkit registration used to keep
    only the last one. Names that do not collide are left unchanged,
    including a sanitized tool registered on its own.

    The server-side name (``MCPTool._tool.name``) is never modified. When
    several raw identities share a sanitized spelling, the identity whose
    raw name was already legal keeps that spelling. Every other identity
    gets a suffix derived from its original server and tool name, so the
    result does not depend on registration order. The suffix is kept
    within 64 characters for the provider limit in #3372. Identical raw
    names stay identical and the toolkit still overwrites true duplicates.

    Args:
        tools (`list[ToolBase]`):
            Tools that will share one model-facing namespace. Tool names
            are updated in place.

    Raises:
        ValueError: If a collision cannot be given a free name inside
            the provider limit.
    """
    if len(tools) < 2:
        return

    buckets: dict[str, list[ToolBase]] = defaultdict(list)
    for tool in tools:
        buckets[tool.name].append(tool)

    # Identity -> collision spellings it currently shares with someone else.
    collision_bases: dict[tuple[str, str], set[str]] = defaultdict(set)
    for base_name, bucket in buckets.items():
        identities: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for tool in bucket:
            identity = _mcp_raw_identity(tool)
            if identity is None or identity in seen:
                continue
            seen.add(identity)
            identities.append(identity)
        if len(identities) < 2:
            continue

        owner: tuple[str, str] | None = None
        for identity in identities:
            if _owns_unsanitized_base(base_name, identity[0], identity[1]):
                owner = identity
                break
        # A function registered under the sanitized spelling keeps that
        # explicit name. Every MCP identity then takes a suffix so none
        # of them is overwritten by the function or by each other.
        if any(_mcp_raw_identity(tool) is None for tool in bucket):
            owner = None
        for identity in identities:
            if identity != owner:
                collision_bases[identity].add(base_name)

    if not collision_bases:
        return

    tools_by_identity: dict[tuple[str, str], list[ToolBase]] = defaultdict(
        list,
    )
    for tool in tools:
        identity = _mcp_raw_identity(tool)
        if identity in collision_bases:
            tools_by_identity[identity].append(tool)

    holders: dict[str, set[tuple[str, str] | None]] = defaultdict(set)
    for tool in tools:
        holders[tool.name].add(_mcp_raw_identity(tool))

    # One base per identity. Prefer the canonical sanitized spelling when
    # that is the collision, so a copy that was already suffixed converges
    # on the same name instead of growing a second suffix.
    pending: list[tuple[str, tuple[str, str]]] = []
    for identity, bases in collision_bases.items():
        canonical = _canonical_mcp_name(identity[0], identity[1])
        base_name = canonical if canonical in bases else min(bases)
        pending.append((base_name, identity))
    pending.sort(key=lambda item: (item[0], item[1][0], item[1][1]))

    planned: list[tuple[ToolBase, str, tuple[str, str]]] = []
    for base_name, identity in pending:
        new_name = _allocate_disambiguated_name(base_name, identity, holders)
        old_names = {tool.name for tool in tools_by_identity[identity]}
        for old_name in old_names:
            holders[old_name].discard(identity)
            if not holders[old_name]:
                del holders[old_name]
        holders[new_name].add(identity)
        for tool in tools_by_identity[identity]:
            planned.append((tool, new_name, identity))

    for tool, new_name, identity in planned:
        if tool.name == new_name:
            continue
        logger.debug(
            "MCP tool name '%s' on server '%s' collides with another tool "
            "after sanitization as '%s'; exposing it as '%s'.",
            identity[1],
            identity[0],
            tool.name,
            new_name,
        )
        tool.name = new_name


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
        input_schema: dict | type[BaseModel] | None = None,
        is_concurrency_safe: bool = True,
        is_read_only: bool = False,
        is_state_injected: bool = False,
        middlewares: list[ToolMiddlewareBase] | None = None,
        permission: PermissionDecision | None = None,
    ) -> None:
        """Initialize the FunctionTool.

        Args:
            func (`Callable`):
                The Python function to wrap.
            name (`str | None`, optional):
                Custom tool name. If None, uses the function name.
            description (`str | None`, optional):
                Custom tool description. If None, extracts from docstring.
            input_schema (`dict | type[BaseModel] | None`, optional):
                Custom input schema for the tool, either a JSON schema
                dict or a pydantic ``BaseModel`` subclass (converted via
                its ``model_json_schema()``). If None, generates the
                schema from the function's type annotations and
                docstring, where constraints (e.g. enums, value ranges)
                can be expressed with ``typing.Literal`` and
                ``typing.Annotated`` with ``pydantic.Field``.
            is_concurrency_safe (`bool`, optional):
                Whether this tool is safe to call concurrently.
            is_read_only (`bool`, optional):
                Whether this tool only reads data without side effects.
            is_state_injected (`bool`, optional):
                Whether this tool requires agent state injection.
            middlewares (`list[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            permission (`PermissionDecision | None`, optional):
                The permission decision of this tool. If not provided, the
                user will be asked to confirm the tool call.
        """
        super().__init__(middlewares=middlewares)
        self.name = name or _get_callable_name(func)
        self.description = description or _extract_func_description(
            _get_callable_docstring(func),
        )
        if isinstance(input_schema, type) and issubclass(
            input_schema,
            BaseModel,
        ):
            input_schema = _remove_title_field(
                input_schema.model_json_schema(),
            )
        self.input_schema = input_schema or _extract_input_schema(func)
        self.is_concurrency_safe = is_concurrency_safe
        self.is_read_only = is_read_only
        self.is_state_injected = is_state_injected
        self.is_external_tool = False
        self.is_mcp = False
        self._func = func
        self._permission = permission

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool usage.

        Returns:
            `PermissionDecision`:
                The permission decision given in the constructor, or asking
                the user to confirm the tool call if not provided.
        """
        if self._permission is not None:
            return self._permission

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
        result = self._func(**kwargs)
        if inspect.isawaitable(result):
            result = await result

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
        client_gen: Callable[..., AbstractAsyncContextManager[Any]]
        | None = None,
        session: Any | None = None,
        timeout: float | None = None,
        middlewares: list[ToolMiddlewareBase] | None = None,
        stream_progress: bool = False,
    ) -> None:
        """Initialize the MCPTool.

        Args:
            mcp_name (`str`):
                The name of the MCP server instance.
            tool (`mcp.types.Tool`):
                The MCP tool definition.
            client_gen (`Callable[..., AbstractAsyncContextManager[Any]] \
            | None`, optional):
                The MCP client generator function for stateless clients.
                Its context manager should yield a connected ``mcp.Client``.
                Legacy transport tuples are accepted with a deprecation
                warning; only their read and write streams are used.
                Either this or ``session`` must be provided.
            session (`mcp.Client | None`, optional):
                The MCP high-level client for stateful clients.
                Either this or ``client_gen`` must be provided.
            timeout (`float | None`, optional):
                The timeout in seconds for tool execution.
            middlewares (`list[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            stream_progress (`bool`, defaults to `False`):
                Return a progress/result stream instead of the final chunk.
        """
        super().__init__(middlewares=middlewares)
        self.mcp_name = mcp_name
        self.stream_progress = stream_progress

        # LLM providers enforce ^[a-zA-Z0-9_-]+$ on tool names.
        # mcp_name is validated in MCPClient.model_post_init;
        # tool.name comes from the MCP server and may contain dots,
        # colons, etc. — replace illegal chars with "x" (not "_")
        # to avoid collisions with the "__" separator.
        # self._tool.name retains the original for server-side calls.
        # A tool registered on its own keeps this spelling. Distinct raw
        # names that sanitize to the same spelling are separated later by
        # disambiguate_sanitized_mcp_names, without touching _tool.name.
        sanitized_tool = _MCP_NAME_PATTERN.sub("x", tool.name)
        self.name = f"mcp__{mcp_name}__{sanitized_tool}"
        if sanitized_tool != tool.name:
            logger.debug(
                "MCP tool name sanitized: '%s' -> '%s'.",
                tool.name,
                self.name,
            )

        self.description = tool.description or ""

        # Preserve the full input_schema (including $defs, anyOf, oneOf,
        # etc.) rather than only copying "properties" and "required",
        # which would silently drop any nested type definitions that the
        # LLM needs to resolve $ref pointers.
        _schema = dict(tool.input_schema) if tool.input_schema else {}
        _schema.setdefault("type", "object")
        _schema.setdefault("properties", {})
        _schema.setdefault("required", [])
        self.input_schema = _schema

        # By default
        self.is_concurrency_safe = False
        self.is_external_tool = False

        # Extract is_read_only from MCP tool annotations
        self.is_read_only = False
        if tool.annotations:
            self.is_read_only = tool.annotations.read_only_hint or False

        # Store MCP tool and connection info
        self._tool = tool
        self._client_gen = client_gen
        self._session = session

        if timeout:
            self._timeout = float(timeout)
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
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Return the final result, or stream when ``stream_progress=True``.

        Progress is best-effort snapshot delivery, not a lossless event log.
        At most 1024 pending progress updates are retained; overflow drops
        the oldest updates without reporting a dropped count. Consumers
        must not depend on receiving every update. Buffer overflow does not
        discard the final result, which is emitted separately.

        Args:
            **kwargs: Arguments to pass to the MCP tool.

        Returns:
            `ToolChunk | AsyncGenerator[ToolChunk, None]`:
                The final result by default. When streaming is enabled,
                progress chunks followed by the converted tool result.
        """
        if self.stream_progress:
            return self._stream_call(kwargs)
        final_chunk: ToolChunk | None = None
        async for chunk in self._stream_call(kwargs):
            if chunk.is_last:
                final_chunk = chunk
        if final_chunk is None:
            raise RuntimeError(
                f"MCP tool {self.name!r} returned no final chunk.",
            )
        return final_chunk

    async def _stream_call(
        self,
        kwargs: dict[str, Any],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Run one MCP call and expose request-scoped progress updates."""
        # Progress is a snapshot; retain the newest 1024 pending updates.
        progress_queue: asyncio.Queue[ToolChunk] = asyncio.Queue(maxsize=1024)
        accepting_progress = True

        async def _on_progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            if accepting_progress:
                if progress_queue.full():
                    progress_queue.get_nowait()
                progress_queue.put_nowait(
                    ToolChunk(
                        content=[],
                        state=ToolResultState.RUNNING,
                        is_last=False,
                        metadata={
                            "mcp_progress": {
                                "progress": progress,
                                "total": total,
                                "message": message,
                            },
                        },
                    ),
                )

        async def _invoke() -> mcp.types.CallToolResult:
            async with AsyncExitStack() as stack:
                client = self._session
                if self._client_gen:
                    client = await stack.enter_async_context(
                        self._client_gen(),
                    )
                    if isinstance(client, tuple):
                        warnings.warn(
                            "MCPTool client_gen yielding transport tuples "
                            "is deprecated; yield a connected mcp.Client "
                            "instead.",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                        client = await stack.enter_async_context(
                            mcp.ClientSession(client[0], client[1]),
                        )
                        await client.initialize()
                return await client.call_tool(
                    self._tool.name,
                    arguments=kwargs,
                    read_timeout_seconds=self._timeout,
                    progress_callback=_on_progress,
                )

        call_task = asyncio.create_task(_invoke())
        progress_task: asyncio.Task[ToolChunk] | None = None
        try:
            while not call_task.done():
                progress_task = asyncio.create_task(progress_queue.get())
                done, _ = await asyncio.wait(
                    {call_task, progress_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if progress_task in done:
                    yield progress_task.result()
                    progress_task = None
                else:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass
                    progress_task = None

            accepting_progress = False
            while not progress_queue.empty():
                yield progress_queue.get_nowait()
            result = await call_task

            content = self._convert_mcp_content_to_blocks(result.content)
            if result.structured_content is not None and not any(
                isinstance(block, TextBlock) for block in content
            ):
                content.append(
                    TextBlock(
                        text=json.dumps(
                            result.structured_content,
                            ensure_ascii=False,
                        ),
                    ),
                )

            yield ToolChunk(
                content=content,
                state=(
                    ToolResultState.ERROR
                    if result.is_error
                    else ToolResultState.RUNNING
                ),
                is_last=True,
            )
        finally:
            accepting_progress = False
            # Repeated AnyIO cancellation must not interrupt the SDK's
            # cancellation notification while we wait for its cleanup.
            with anyio.CancelScope(shield=True):
                if progress_task is not None and not progress_task.done():
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass
                if not call_task.done():
                    call_task.cancel()
                    try:
                        await call_task
                    except asyncio.CancelledError:
                        pass

    @staticmethod
    def _convert_mcp_content_to_blocks(
        mcp_content_blocks: list,
    ) -> list[TextBlock | DataBlock]:
        """Convert MCP content directly to AgentScope message blocks."""
        blocks: list[TextBlock | DataBlock] = []
        for content in mcp_content_blocks:
            if isinstance(content, mcp.types.TextContent):
                blocks.append(TextBlock(text=content.text))
            elif isinstance(
                content,
                (mcp.types.ImageContent, mcp.types.AudioContent),
            ):
                blocks.append(
                    DataBlock(
                        source=Base64Source(
                            media_type=content.mime_type,
                            data=content.data,
                        ),
                    ),
                )
            elif isinstance(content, mcp.types.EmbeddedResource):
                if isinstance(
                    content.resource,
                    mcp.types.TextResourceContents,
                ):
                    blocks.append(
                        TextBlock(
                            text=content.resource.model_dump_json(
                                indent=2,
                            ),
                        ),
                    )
                else:
                    logger.error(
                        "Unsupported EmbeddedResource content type: %s.",
                        type(content.resource),
                    )
            elif isinstance(
                content,
                (mcp.types.ResourceLink, mcp.types.ResourceContents),
            ):
                media_type = content.mime_type
                if isinstance(content, mcp.types.ResourceLink):
                    media_type = media_type or "application/octet-stream"
                try:
                    url = AnyUrl(str(content.uri))
                except ValidationError:
                    message = (
                        f"<unconvertible MCP resource URI: {content.uri}>"
                    )
                    logger.warning(message)
                    blocks.append(TextBlock(text=message))
                    continue
                blocks.append(
                    DataBlock(
                        source=URLSource(media_type=media_type, url=url),
                    ),
                )
            else:
                logger.warning(
                    "Unsupported content type: %s. Skipping this content.",
                    type(content),
                )
        return blocks
