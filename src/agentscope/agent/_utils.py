# -*- coding: utf-8 -*-
"""The utility classes used in building the agent class."""
from dataclasses import dataclass
from typing import Literal

from ..message import ToolCallBlock


@dataclass
class _ToolCallBatch:
    """A batch of tool calls that execute either sequentially or
    concurrently."""

    type: Literal["sequential", "concurrent"]
    """The batch type"""
    tool_calls: list[ToolCallBlock]
    """The list of tool calls in the batch."""
