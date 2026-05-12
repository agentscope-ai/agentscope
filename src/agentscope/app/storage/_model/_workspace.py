# -*- coding: utf-8 -*-
"""The workspace record class."""
from pydantic import BaseModel

from ._base import _RecordBase


class WorkspaceBase(BaseModel):
    """Input data for creating or updating a workspace."""

    id: str | None = None
    agent_id: str
    data: dict


class WorkspaceRecord(_RecordBase):
    """The workspace record model."""

    user_id: str

    agent_id: str

    data: dict
    """The workspace config data, used to initialize the workspace."""
