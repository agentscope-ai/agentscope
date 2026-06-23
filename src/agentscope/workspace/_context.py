# -*- coding: utf-8 -*-
"""Actor-scoped views and run handles for shared workspaces."""

# pylint: disable=missing-function-docstring

from collections.abc import Awaitable, Callable
import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..mcp import MCPClient, MCPProvider
from ..message import Msg, ToolResultBlock
from ..skill import Skill
from ..tool import ToolBase
from ._base import WorkspaceBase


class WorkspaceActor(BaseModel):
    """Identity and capabilities used for one workspace run."""

    user_id: str
    agent_id: str
    session_id: str
    team_id: str | None = None
    role: Literal["owner", "leader", "worker", "viewer"] = "viewer"
    capabilities: set[str] = Field(default_factory=set)


@dataclass(frozen=True)
class WorkspaceView:
    """Actor-bound facade over a shared physical workspace."""

    runtime: WorkspaceBase
    actor: WorkspaceActor

    @property
    def workspace_id(self) -> str:
        return self.runtime.workspace_id

    @property
    def workdir(self) -> str:
        return self.runtime.actor_workdir(self.actor)

    async def get_instructions(self) -> str:
        return await self.runtime.get_instructions()

    async def list_tools(self) -> list[ToolBase]:
        return await self.runtime.list_tools_for_actor(self.actor)

    async def list_skills(self) -> list[Skill]:
        return await self.runtime.list_skills()

    async def list_mcps(self) -> list[MCPClient]:
        return await self.runtime.list_mcps(
            agent_id=self.actor.agent_id,
            session_id=self.actor.session_id,
        )

    async def list_mcp_providers(self) -> list[MCPProvider]:
        return await self.runtime.list_mcp_providers(
            agent_id=self.actor.agent_id,
            session_id=self.actor.session_id,
        )

    async def offload_context(self, session_id: str, msgs: list[Msg]) -> str:
        if session_id != self.actor.session_id:
            raise PermissionError("Cannot offload another session's context.")
        return await self.runtime.offload_context(session_id, msgs)

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        if session_id != self.actor.session_id:
            raise PermissionError("Cannot offload another session's result.")
        return await self.runtime.offload_tool_result(session_id, tool_result)

    async def publish_file(self, source: str, destination: str) -> str:
        if (
            self.actor.role not in {"owner", "leader"}
            and "workspace.publish" not in self.actor.capabilities
        ):
            raise PermissionError("Actor cannot publish shared files.")
        return await self.runtime.publish_file(
            self.actor,
            source,
            destination,
        )


@dataclass
class WorkspaceRunHandle:
    """Idempotently release resources acquired for one agent run."""

    view: WorkspaceView
    lease_id: str
    _release: Callable[[], Awaitable[None]]
    _heartbeat: asyncio.Task[None] | None = None
    _closed: bool = field(default=False, init=False)

    @property
    def workspace(self) -> WorkspaceBase:
        return self.view.runtime

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
        await self._release()

    async def __aenter__(self) -> "WorkspaceRunHandle":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
