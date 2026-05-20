# -*- coding: utf-8 -*-
"""The memory offloading module."""

from ._offloading_base import MemoryOffloadingBase
from ._in_memory_offloading import InMemoryMemoryOffloading

__all__ = [
    "MemoryOffloadingBase",
    "InMemoryMemoryOffloading",
]
