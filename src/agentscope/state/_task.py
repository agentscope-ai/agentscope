# -*- coding: utf-8 -*-
"""The task class."""
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Task(BaseModel):
    """The agent task."""

    subject: str
    """The subject of the task."""

    description: str
    """The task description."""

    metadata: dict[str, Any]
    """The additional metadata of the task."""

    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    """The created timestamp."""

    state: Literal["pending", "in_progress", "completed"] = "pending"
    """The task state."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The task identifier."""

    owner: str | None = None
    """The owner of the task."""

    blocks: list[str] = Field(default_factory=lambda: [])
    """The task ids blocked by this task."""

    blocked_by: list[str] = Field(default_factory=lambda: [])
    """The task ids blocking this task."""
