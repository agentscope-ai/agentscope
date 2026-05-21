# -*- coding: utf-8 -*-
"""The base attributes used in storage."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class _RecordBase(BaseModel):
    """The base class for all records."""

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for the credential.",
    )

    updated_at: datetime = Field(
        default_factory=datetime.now,
    )
    """The updated time."""

    created_at: datetime = Field(
        default_factory=datetime.now,
    )
    """The created time."""
