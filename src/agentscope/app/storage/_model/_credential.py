# -*- coding: utf-8 -*-
"""The credential record."""
import uuid

from pydantic import BaseModel, Field

from ._base import _RecordBase


class CredentialBase(BaseModel):
    """Input data for creating or updating a credential."""

    id: str | None = None
    data: dict


class CredentialRecord(_RecordBase):
    """The credential model used for storing credentials."""

    user_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
    )

    data: dict
    """The credential data."""
