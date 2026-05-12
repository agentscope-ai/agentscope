# -*- coding: utf-8 -*-
"""The workspace record class."""
from ._base import _RecordBase


class WorkspaceRecord(_RecordBase):
    """The workspace record model."""

    user_id: str

    agent_id: str

    data: dict
    """The workspace config data, used to initialize the workspace."""
