# -*- coding: utf-8 -*-
"""Built-in slash-command metadata and parsing."""

from ._dispatcher import (
    CommandContext,
    CommandResult,
    dispatch_command,
)
from ._registry import (
    BUILTIN_COMMANDS,
    CommandMatch,
    CommandSpec,
    list_commands,
    parse_command,
)

__all__ = [
    "BUILTIN_COMMANDS",
    "CommandContext",
    "CommandMatch",
    "CommandResult",
    "CommandSpec",
    "dispatch_command",
    "list_commands",
    "parse_command",
]
