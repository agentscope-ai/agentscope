# -*- coding: utf-8 -*-
"""The credential record."""
from pydantic import Field

from ._base import _RecordBase
from ...._utils._common import _id_factory


class CredentialRecord(_RecordBase):
    """The credential model used for storing credentials."""

    user_id: str = Field(
        default_factory=_id_factory,
    )

    data: dict
    """The credential data."""
