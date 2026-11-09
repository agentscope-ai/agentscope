# -*- coding: utf-8 -*-
"""The permission context module."""
from pydantic import BaseModel, Field

from ._rule import PermissionRule
from ._types import PermissionMode


class AdditionalWorkingDirectory(BaseModel):
    """An additional directory included in permission scope.

    Working directories are used to determine which file paths should be
    automatically allowed in ACCEPT_EDITS mode.
    """

    path: str
    """Absolute path to the directory."""

    source: str
    """Where this directory permission originated from
    (e.g., 'userSettings', 'session')."""


class PermissionContext(BaseModel):
    """Context for permission checking.

    Contains the permission mode, working directories, and all configured
    permission rules organized by behavior type (allow, deny, ask).
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    """The current permission mode."""

    working_directories: dict[str, AdditionalWorkingDirectory] = Field(
        default_factory=dict,
    )
    """Additional directories allowed for file operations, keyed by path."""

    allow_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that allow tool execution, keyed by tool name."""

    deny_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that deny tool execution, keyed by tool name."""

    ask_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that require user confirmation, keyed by tool name."""
