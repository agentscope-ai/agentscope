# -*- coding: utf-8 -*-
"""The storage module in agentscope."""

from ._base import StorageBase
from ._file import LocalJSONStorage
from ._redis import RedisStorage

__all__ = [
    "StorageBase",
    "LocalJSONStorage",
    "RedisStorage",
]
