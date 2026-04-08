# -*- coding: utf-8 -*-
"""The tool response class."""
import uuid
from dataclasses import dataclass, field
from typing import Optional, List

from ..message import DataBlock, TextBlock


@dataclass
class ToolResponse:
    """The result chunk of a tool call."""

    content: List[TextBlock | DataBlock]
    """The execution output of the tool function."""

    metadata: Optional[dict] = None
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    stream: bool = False
    """Whether the tool output is streamed."""

    is_last: bool = True
    """Whether this is the last response in a stream tool execution."""

    is_interrupted: bool = False
    """Whether the tool execution is interrupted."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """The identity of the tool response."""
