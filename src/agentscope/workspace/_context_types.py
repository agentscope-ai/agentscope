# -*- coding: utf-8 -*-
"""Structural types shared without importing workspace context models."""

from typing import Protocol


class WorkspaceActorProtocol(Protocol):
    """Identity fields required by actor-aware workspace operations."""

    user_id: str
    agent_id: str
    session_id: str
    team_id: str | None
    role: str
    capabilities: set[str]
