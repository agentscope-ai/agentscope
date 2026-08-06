# -*- coding: utf-8 -*-
"""In-process session bookkeeping (DESIGN.md §9, §14, §18).

One :class:`Session` per ACP ``sessionId``. The ACP session id *is*
``AgentState.session_id`` (receipt invariant a) — one stable id shared
end-to-end. There is deliberately no multi-tenant session manager here:
the whole registry is a dict on the connection.
"""
import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from acp import RequestError

from .bridge import OpRegistry
from .translate import Translator

if TYPE_CHECKING:
    from acp.schema import ClientCapabilities

    from agentscope.agent import Agent
    from agentscope.state import AgentState


@dataclass
class Session:
    """Everything the kernel tracks for one ACP session."""

    id: str
    """The ACP sessionId == ``AgentState.session_id`` (invariant a)."""

    agent: "Agent"
    state: "AgentState"
    cwd: str

    caps: "ClientCapabilities | None"
    """The initialize-time client capability snapshot (invariant b)."""

    authority: str
    """``"shell"`` or ``"workspace"`` — part of the snapshot."""

    ops: OpRegistry
    """Per-session operation registry (invariant c)."""

    translator: Translator = field(init=False)

    turn_task: asyncio.Task | None = None
    """The child task running the current turn, if any."""

    last_reply_id: str | None = None
    """The reply_id of the most recent turn (for interrupt-on-parked)."""

    _turn_active: bool = False

    def __post_init__(self) -> None:
        self.translator = Translator(self.ops)

    def try_begin_turn(self) -> bool:
        """Acquire the single-active-turn guard (DESIGN.md §18).

        One ``Agent``/``AgentState`` must never be driven by two
        concurrent ``reply_stream`` runs.
        """
        if self._turn_active:
            return False
        self._turn_active = True
        return True

    def end_turn(self) -> None:
        """Release the single-active-turn guard."""
        self._turn_active = False
        self.turn_task = None


class SessionManager:
    """The connection-scoped ``dict[SessionId, Session]``."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def add(self, session: Session) -> None:
        """Register a freshly minted session."""
        self._sessions[session.id] = session

    def get(self, session_id: str) -> Session:
        """Look up a session or fail with a JSON-RPC error."""
        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError.invalid_params(
                {"sessionId": f"unknown session: {session_id}"},
            )
        return session
