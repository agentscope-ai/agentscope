# -*- coding: utf-8 -*-
"""The base attributes used in storage."""
from datetime import datetime

from pydantic import BaseModel, Field

from ...._utils._common import _generate_id


class _RecordBase(BaseModel):
    """The base class for all records."""

    id: str = Field(
        default_factory=_generate_id,
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
