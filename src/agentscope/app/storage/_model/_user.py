# -*- coding: utf-8 -*-
"""The user record for storage."""
import uuid

from pydantic import Field

from ._base import _RecordBase


class UserRecord(_RecordBase):
    """The user record."""
