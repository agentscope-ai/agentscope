# -*- coding: utf-8 -*-
"""The utility classes used in building the agent class."""
from dataclasses import dataclass
from typing import Literal
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel

from ..tool import ToolChoice
from ..message import ToolCallBlock, HintBlock, Msg
from ..event import AgentEvent
from .._logging import logger


@dataclass
class _ToolCallBatch:
    """A batch of tool calls that execute either sequentially or
    concurrently."""

    type: Literal["sequential", "concurrent"]
    """The batch type"""
    tool_calls: list[ToolCallBlock]
    """The list of tool calls in the batch."""


class Acting(BaseModel):
    """Next action: execute the given tool calls."""

    tool_calls: list[ToolCallBlock]


class Reasoning(BaseModel):
    """Next action: another model call, with optional hint and tool choice."""

    hint: HintBlock | None = None
    tool_choice: ToolChoice | None = None


class Exit(BaseModel):
    """Next action: end the reply."""

    exit_msg: Msg
    exit_events: list[AgentEvent] | None = None


def _resolve_timezone(name: str) -> tzinfo:
    """Resolve the given timezone name, falling back to UTC when the name is
    invalid or the timezone database is unavailable, e.g. on Windows or slim
    images without the ``tzdata`` package."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Failed to resolve the timezone %s, fallback to UTC. Install the "
            "'tzdata' package if the timezone database is missing.",
            repr(name),
        )
        return timezone.utc
