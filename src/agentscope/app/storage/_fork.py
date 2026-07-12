# -*- coding: utf-8 -*-
"""Small, ordinary-session-only helpers for storage session forking."""

from dataclasses import dataclass

from ._exceptions import (
    SessionForkConflictError,
    SessionForkCorruptedGraphError,
    SessionForkNotFoundError,
)
from ._model import SessionRecord, SessionSource
from ._model._session import default_session_name


def build_fork_session_name(name: str | None) -> str:
    """Return the default display name for a forked session."""
    if name and name.strip():
        return f"{name} (Fork)"
    return default_session_name()


@dataclass(frozen=True)
class SessionForkPlan:
    """Immutable write plan for a regular, non-team session fork."""

    source_session_id: str
    target_session: SessionRecord
    source_messages: tuple[str | bytes, ...]
    target_session_key: str
    target_messages_key: str
    target_session_index_key: str


def validate_regular_fork_source(
    session: SessionRecord,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> None:
    """Validate the source constraints supported by phase one."""
    if session.id != session_id:
        raise SessionForkCorruptedGraphError(
            "The source session record does not match its storage key.",
        )
    if session.user_id != user_id:
        raise SessionForkNotFoundError(
            "The source session does not belong to the requested user.",
        )
    if session.agent_id != agent_id:
        raise SessionForkNotFoundError(
            "The source session does not belong to the requested agent.",
        )
    if session.team_id is not None:
        raise SessionForkConflictError(
            "Team sessions are not supported by the first fork phase.",
        )
    if session.source == SessionSource.SCHEDULE:
        raise SessionForkConflictError(
            "Schedule sessions are not supported by the first fork phase.",
        )
    if session.source_schedule_id is not None:
        raise SessionForkConflictError(
            "Sessions with schedule provenance are not supported by the "
            "first fork phase.",
        )
