# -*- coding: utf-8 -*-
"""Persisted workspace identity and actor bindings."""

from typing import Literal

from pydantic import Field

from ._base import _RecordBase

WorkspaceScope = Literal["session", "agent", "team"]
WorkspaceBackend = Literal["local", "docker", "e2b"]
WorkspaceStatus = Literal["creating", "ready", "closing", "closed", "error"]
WorkspaceRole = Literal["owner", "leader", "worker", "viewer"]


class WorkspaceRecord(_RecordBase):
    """Durable identity of one physical workspace runtime."""

    owner_user_id: str
    scope: WorkspaceScope
    scope_id: str
    backend: WorkspaceBackend
    backend_ref: dict = Field(default_factory=dict)
    status: WorkspaceStatus = "creating"
    generation: int = Field(default=1, ge=1)
    config_version: int = Field(default=1, ge=1)


class WorkspaceBinding(_RecordBase):
    """Authorize one agent session to open a workspace runtime."""

    workspace_id: str
    user_id: str
    agent_id: str
    session_id: str
    team_id: str | None = None
    role: WorkspaceRole = "viewer"
    capabilities: set[str] = Field(default_factory=set)
