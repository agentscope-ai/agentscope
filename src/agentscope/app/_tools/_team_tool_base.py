# -*- coding: utf-8 -*-
"""Base class shared by the team tools."""
from typing import Any, TYPE_CHECKING

from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...tool import ToolBase

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase


class _TeamToolBase(ToolBase):
    """Shared base for the team tools.

    All team tools are constructed at agent assembly time (in
    :func:`get_toolkit`) with the request-scoped ``user_id``,
    ``session_id``, and ``agent_id`` plus ``storage`` + ``message_bus``
    references. Each tool's ``__call__`` does its work directly via
    those two dependencies — there is no intermediate service layer.

    Permissions: all team tools allow themselves unconditionally — the
    agent's authority to call them is already gated by the
    role/source-aware logic inside :func:`get_toolkit` that decides
    which team tools to attach in the first place.
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

    def __init__(
        self,
        storage: "StorageBase",
        message_bus: "MessageBus",
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Bind request-scoped identifiers and shared dependencies.

        Args:
            storage (`StorageBase`):
                Application storage. Each tool reads the current
                session / team records at ``__call__`` time to do
                runtime precondition checks (am I in a team? am I the
                leader?) — this is what lets all four team tools be
                attached unconditionally to ``source='user'`` agents
                without depending on a stale snapshot of ``team_id``
                taken at agent assembly time.
            message_bus (`MessageBus`):
                Application message bus. Tools that deliver
                inter-session messages (``AgentCreate``, ``TeamSay``)
                push HintBlocks + wakeups through it.
            user_id (`str`):
                The owner user id of the calling agent.
            session_id (`str`):
                The current session id of the calling agent.
            agent_id (`str`):
                The id of the agent invoking the tool.
        """
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id

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
