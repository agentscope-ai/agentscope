# -*- coding: utf-8 -*-
"""Actor-scoped MCP connection pooling."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, cast, TYPE_CHECKING

import mcp.types
from pydantic import BaseModel, Field

from ..permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ._mcp_client import MCPClient
from ._provider import MCPProvider

if TYPE_CHECKING:
    from ..tool import ToolBase, ToolChunk
else:
    ToolBase = Any
    ToolChunk = Any


class MCPDefinition(BaseModel):
    """Serializable MCP configuration with a stable revision identity."""

    id: str
    workspace_id: str
    workspace_generation: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    client_spec: dict[str, Any]

    @classmethod
    def from_client(
        cls,
        *,
        workspace_id: str,
        client: MCPClient,
        workspace_generation: int = 1,
        revision: int = 1,
        definition_id: str | None = None,
    ) -> "MCPDefinition":
        """Build a definition by copying only a client's public fields."""
        return cls(
            id=definition_id or client.name,
            workspace_id=workspace_id,
            workspace_generation=workspace_generation,
            revision=revision,
            client_spec=client.model_dump(mode="json"),
        )

    def create_client(self) -> MCPClient:
        """Create a connection owner with no shared private runtime state."""
        return MCPClient.model_validate(self.client_spec)

    @property
    def is_stateful(self) -> bool:
        """Whether clients from this definition retain state."""
        return bool(self.client_spec["is_stateful"])

    @property
    def name(self) -> str:
        """Return the model-facing MCP server name."""
        return str(self.client_spec["name"])


@dataclass(frozen=True)
class MCPConnectionKey:
    """Complete isolation key for one stateful MCP connection."""

    workspace_id: str
    workspace_generation: int
    definition_id: str
    definition_revision: int
    agent_id: str
    session_id: str

    @classmethod
    def for_actor(
        cls,
        definition: MCPDefinition,
        *,
        agent_id: str,
        session_id: str,
    ) -> "MCPConnectionKey":
        """Build the complete isolation key for an actor and session."""
        return cls(
            workspace_id=definition.workspace_id,
            workspace_generation=definition.workspace_generation,
            definition_id=definition.id,
            definition_revision=definition.revision,
            agent_id=agent_id,
            session_id=session_id,
        )


class MCPResourcePolicy(BaseModel):
    """Resource limits applied before a new MCP connection is created."""

    max_stateful_per_session: int = Field(default=4, ge=1)
    max_stateful_per_agent: int = Field(default=8, ge=1)
    max_stateful_per_workspace: int = Field(default=32, ge=1)
    max_connecting: int = Field(default=4, ge=1)
    max_inflight_per_connection: int = Field(default=1, ge=1)
    max_stateless_calls: int = Field(default=32, ge=1)
    idle_ttl: float = Field(default=900.0, gt=0)
    failed_ttl: float = Field(default=30.0, ge=0)
    connect_timeout: float = Field(default=30.0, gt=0)


@dataclass
class _ConnectionEntry:
    client: MCPClient
    operation_limit: asyncio.Semaphore
    active_operations: int = 0
    last_used: float = field(default_factory=time.monotonic)


class MCPConnectionPool:
    """Own MCP clients and isolate stateful sessions by actor/session."""

    def __init__(self, policy: MCPResourcePolicy | None = None) -> None:
        self.policy = policy or MCPResourcePolicy()
        self._entries: dict[MCPConnectionKey, _ConnectionEntry] = {}
        self._connecting: dict[
            MCPConnectionKey,
            asyncio.Task[_ConnectionEntry],
        ] = {}
        self._failures: dict[MCPConnectionKey, tuple[float, BaseException]] = (
            {}
        )
        self._retired: set[MCPConnectionKey] = set()
        self._lock = asyncio.Lock()
        self._connect_limit = asyncio.Semaphore(self.policy.max_connecting)
        self._stateless_limit = asyncio.Semaphore(
            self.policy.max_stateless_calls,
        )

    async def _create_entry(
        self,
        definition: MCPDefinition,
    ) -> _ConnectionEntry:
        client = definition.create_client()
        try:
            async with self._connect_limit:
                await asyncio.wait_for(
                    client.connect(),
                    timeout=self.policy.connect_timeout,
                )
            return _ConnectionEntry(
                client=client,
                operation_limit=asyncio.Semaphore(
                    self.policy.max_inflight_per_connection,
                ),
            )
        except BaseException:
            if client.is_connected:
                await client.close()
            raise

    def _check_quota(self, key: MCPConnectionKey) -> None:
        keys = [*self._entries, *self._connecting]
        if (
            sum(
                item.workspace_id == key.workspace_id
                and item.session_id == key.session_id
                for item in keys
            )
            >= self.policy.max_stateful_per_session
        ):
            raise RuntimeError("MCP session connection quota exceeded.")
        if (
            sum(
                item.workspace_id == key.workspace_id
                and item.agent_id == key.agent_id
                for item in keys
            )
            >= self.policy.max_stateful_per_agent
        ):
            raise RuntimeError("MCP agent connection quota exceeded.")
        if (
            sum(item.workspace_id == key.workspace_id for item in keys)
            >= self.policy.max_stateful_per_workspace
        ):
            raise RuntimeError("MCP workspace connection quota exceeded.")

    async def _get_or_connect(
        self,
        key: MCPConnectionKey,
        definition: MCPDefinition,
    ) -> _ConnectionEntry:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return entry

            failure = self._failures.get(key)
            if failure is not None:
                failed_at, error = failure
                if time.monotonic() - failed_at < self.policy.failed_ttl:
                    raise RuntimeError(
                        "MCP connection is in failure backoff.",
                    ) from error
                self._failures.pop(key, None)

            task = self._connecting.get(key)
            if task is None:
                self._check_quota(key)
                task = asyncio.create_task(
                    self._create_entry(definition),
                )
                self._connecting[key] = task

        try:
            entry = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The shared connection attempt remains available to another
            # waiter; one cancelled caller must not poison its actor scope.
            raise
        except BaseException as error:
            async with self._lock:
                if self._connecting.get(key) is task:
                    self._connecting.pop(key, None)
                    self._failures[key] = (time.monotonic(), error)
            raise

        async with self._lock:
            if self._connecting.get(key) is task:
                self._connecting.pop(key, None)
                self._entries[key] = entry
            return self._entries.get(key, entry)

    @asynccontextmanager
    async def operation(
        self,
        definition: MCPDefinition,
        key: MCPConnectionKey,
    ) -> AsyncIterator[MCPClient]:
        """Pin a client for the full duration of one schema/tool operation."""
        if not definition.is_stateful:
            async with self._stateless_limit:
                yield definition.create_client()
            return

        entry = await self._get_or_connect(key, definition)
        async with entry.operation_limit:
            async with self._lock:
                entry.active_operations += 1
                entry.last_used = time.monotonic()
            try:
                yield entry.client
            finally:
                close_client = False
                async with self._lock:
                    entry.active_operations -= 1
                    entry.last_used = time.monotonic()
                    if entry.active_operations == 0 and key in self._retired:
                        self._entries.pop(key, None)
                        self._retired.discard(key)
                        close_client = True
                if close_client:
                    await entry.client.close()

    async def close_scope(
        self,
        *,
        workspace_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Close idle connections matching an ownership scope."""
        async with self._lock:
            matches = [
                (key, entry)
                for key, entry in self._entries.items()
                if key.workspace_id == workspace_id
                and (agent_id is None or key.agent_id == agent_id)
                and (session_id is None or key.session_id == session_id)
            ]
            selected = [
                item for item in matches if item[1].active_operations == 0
            ]
            self._retired.update(
                key for key, entry in matches if entry.active_operations > 0
            )
            for key, _ in selected:
                self._entries.pop(key, None)
        await asyncio.gather(
            *(entry.client.close() for _, entry in selected),
            return_exceptions=True,
        )
        return len(selected)

    async def close_definition(
        self,
        *,
        workspace_id: str,
        definition_id: str,
    ) -> int:
        """Close idle connections for a removed or revised definition."""
        async with self._lock:
            matches = [
                (key, entry)
                for key, entry in self._entries.items()
                if key.workspace_id == workspace_id
                and key.definition_id == definition_id
            ]
            selected = [
                item for item in matches if item[1].active_operations == 0
            ]
            self._retired.update(
                key for key, entry in matches if entry.active_operations > 0
            )
            for key, _ in selected:
                self._entries.pop(key, None)
        await asyncio.gather(
            *(entry.client.close() for _, entry in selected),
            return_exceptions=True,
        )
        return len(selected)

    async def sweep_idle(self, now: float | None = None) -> int:
        """Close connections that are idle beyond the configured TTL."""
        cutoff = (
            now if now is not None else time.monotonic()
        ) - self.policy.idle_ttl
        async with self._lock:
            selected = [
                (key, entry)
                for key, entry in self._entries.items()
                if entry.active_operations == 0 and entry.last_used <= cutoff
            ]
            for key, _ in selected:
                self._entries.pop(key, None)
        await asyncio.gather(
            *(entry.client.close() for _, entry in selected),
            return_exceptions=True,
        )
        return len(selected)

    async def close(self) -> None:
        """Cancel pending connects and close entries owned by this process."""
        async with self._lock:
            connecting = list(self._connecting.values())
            self._connecting.clear()
        for task in connecting:
            task.cancel()
        if connecting:
            await asyncio.gather(*connecting, return_exceptions=True)
        workspace_ids = {key.workspace_id for key in self._entries}
        for workspace_id in workspace_ids:
            await self.close_scope(workspace_id=workspace_id)

    def provider(
        self,
        definition: MCPDefinition,
        *,
        agent_id: str,
        session_id: str,
    ) -> "ScopedMCPProvider":
        """Create a provider bound to one definition and actor scope."""
        return ScopedMCPProvider(
            pool=self,
            definition=definition,
            key=MCPConnectionKey.for_actor(
                definition,
                agent_id=agent_id,
                session_id=session_id,
            ),
        )


class ScopedMCPTool:
    """Stable tool descriptor that leases its live client per invocation."""

    is_mcp = True
    is_state_injected = False

    def __init__(
        self,
        *,
        pool: MCPConnectionPool,
        definition: MCPDefinition,
        key: MCPConnectionKey,
        raw_tool: mcp.types.Tool,
        descriptor: ToolBase,
    ) -> None:
        self.name = descriptor.name
        self.description = descriptor.description
        self.input_schema = descriptor.input_schema
        self.is_concurrency_safe = False
        self.is_external_tool = False
        self.is_read_only = descriptor.is_read_only
        self.mcp_name = definition.name
        self._pool = pool
        self._definition = definition
        self._key = key
        self._raw_name = raw_tool.name

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Mirror the default MCP read-only/confirmation policy."""
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="This is a read-only MCP tool. Allowing execution.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="MCP tools must be explicitly allowed by the user.",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Lease a live client for the complete upstream tool call."""
        async with self._pool.operation(self._definition, self._key) as client:
            tool = await client.get_tool(self._raw_name)
            return await tool.call(**kwargs)

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Invoke the proxy using the standard tool callable surface."""
        return await self.call(**kwargs)

    @property
    def raw_name(self) -> str:
        """Return the upstream MCP tool name used in gateway routes."""
        return self._raw_name


class ScopedMCPProvider(MCPProvider):
    """Materialize actor-bound proxy tools for one MCP definition."""

    def __init__(
        self,
        *,
        pool: MCPConnectionPool,
        definition: MCPDefinition,
        key: MCPConnectionKey,
    ) -> None:
        self.name = definition.name
        self._pool = pool
        self._definition = definition
        self._key = key

    @property
    def key(self) -> MCPConnectionKey:
        """Connection identity bound to this provider."""
        return self._key

    async def get_tools(self) -> list[ToolBase]:
        async with self._pool.operation(
            self._definition,
            self._key,
        ) as client:
            raw_tools = await client.list_raw_tools()
            descriptors = [
                await client.get_tool(raw_tool.name) for raw_tool in raw_tools
            ]
        return cast(
            list[ToolBase],
            [
                ScopedMCPTool(
                    pool=self._pool,
                    definition=self._definition,
                    key=self._key,
                    raw_tool=raw_tool,
                    descriptor=descriptor,
                )
                for raw_tool, descriptor in zip(raw_tools, descriptors)
            ],
        )
