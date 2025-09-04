# -*- coding: utf-8 -*-
"""Solution class for evaluation tasks."""

from dataclasses import dataclass, field
from typing import Any

from ..message import (
    ToolResultBlock,
    ToolUseBlock,
    TextBlock,
)
from ..types._json import JSONSerializableObject
from .._utils._mixin import DictMixin


@dataclass
class SolutionOutput(DictMixin):
    """The output of a solution in evaluation task"""

    success: bool
    """Indicates whether the solution is executed successfully. When the
    solution raise exception, this should be set to False."""
    output: JSONSerializableObject
    """The final output of the solution."""
    trajectory: list[ToolUseBlock | ToolResultBlock | TextBlock]
    """The tool calls and results trajectory"""
    meta: dict[str, Any] | None = field(default_factory=lambda: None)
    """Additional metadata for the solution"""

    def __getstate__(self) -> dict[str, Any]:
        """Custom pickling to handle dataclass + DictMixin inheritance."""
        return self.__dict__.copy()

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Custom unpickling to handle dataclass + DictMixin inheritance."""
        self.__dict__.update(state)
