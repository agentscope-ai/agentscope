# -*- coding: utf-8 -*-
"""The credential record."""
from pydantic import Field

from ...._utils._common import _generate_id
from ._base import _RecordBase


class CredentialRecord(_RecordBase):
    """The credential model used for storing credentials."""

    user_id: str = Field(
        default_factory=_generate_id,
    )

    data: dict
    """The credential data."""
