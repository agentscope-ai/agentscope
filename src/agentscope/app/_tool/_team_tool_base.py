# -*- coding: utf-8 -*-
"""Base class shared by the team tools."""
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...tool import ToolBase

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase, TeamRecord
    from ..workspace_manager import WorkspaceManagerBase


@dataclass(frozen=True)
class TeamToolDeps:
    """The request-scoped dependencies every team tool is built with."""

    storage: "StorageBase"
    """Application storage."""

    message_bus: "MessageBus"
    """Application message bus for inter-session delivery."""

    workspace_manager: "WorkspaceManagerBase"
    """Workspace manager, used by tools that provision new sessions."""

    user_id: str
    """The owner user id of the calling agent."""

    session_id: str
    """The current session id of the calling agent."""

    agent_id: str
    """The id of the agent invoking the tool."""


class _TeamToolError(Exception):
    """A failed precondition, rendered by each tool's error wrapper."""


class _TeamToolBase(ToolBase):
    """Shared base for the team tools.

    All team tools are constructed at agent assembly time (in
    :func:`get_toolkit`) from one :class:`TeamToolDeps` bundle. Each
    tool's ``__call__`` does its work directly via those dependencies —
    there is no intermediate service layer.

    Permissions: all team tools allow themselves unconditionally —
    which tools get attached is already role-gated in
    :func:`get_toolkit`, and the leader-side ones re-check their
    precondition at call time via :meth:`_require_leader_team`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, deps: TeamToolDeps) -> None:
        """Unpack the shared dependency bundle onto the instance.

        Args:
            deps (`TeamToolDeps`):
                The request-scoped dependencies, built once per chat
                turn by :func:`get_toolkit`.
        """
        self._storage = deps.storage
        self._message_bus = deps.message_bus
        self._workspace_manager = deps.workspace_manager
        self._user_id = deps.user_id
        self._session_id = deps.session_id
        self._agent_id = deps.agent_id

    async def _require_team(self) -> "TeamRecord":
        """Return this session's team, read fresh at call time.

        Read fresh rather than taken from assembly-time context: the
        leader may have called ``TeamCreate`` earlier in this same
        reply, so a cached view would be stale.

        Returns:
            `TeamRecord`: The team this session participates in.

        Raises:
            `_TeamToolError`: When the session is teamless or its team
                record is gone.
        """
        session = await self._storage.get_session(
            self._user_id,
            self._agent_id,
            self._session_id,
        )
        if session is None or session.team_id is None:
            raise _TeamToolError(
                "this session is not in any team — call TeamCreate first.",
            )
        team = await self._storage.get_team(self._user_id, session.team_id)
        if team is None:
            raise _TeamToolError(f"team {session.team_id} no longer exists.")
        return team

    async def _require_leader_team(self) -> "TeamRecord":
        """Return the team this session **leads**.

        Returns:
            `TeamRecord`: The team whose leader is this session.

        Raises:
            `_TeamToolError`: As :meth:`_require_team`, plus when this
                session is a worker rather than the leader.
        """
        team = await self._require_team()
        if team.session_id != self._session_id:
            raise _TeamToolError(
                "only the team leader can do this; this session is a worker.",
            )
        return team

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow — gating is done by tool-attachment logic.

        Args:
            tool_input (`dict[str, Any]`):
                The arguments the agent passed; ignored here.
            context (`PermissionContext`):
                The active permission context; ignored here.

        Returns:
            `PermissionDecision`:
                An ``ALLOW`` decision with a brief explanation.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed when attached to the "
            f"agent.",
        )
