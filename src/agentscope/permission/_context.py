# -*- coding: utf-8 -*-
"""The permission context module."""
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ._rule import PermissionRule
from ._types import PermissionMode


class AdditionalWorkingDirectory(BaseModel):
    """An additional directory included in permission scope.

    Working directories are used to determine which file paths should be
    automatically allowed when workspace edit grants are enabled.
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

    mode: PermissionMode = PermissionMode.ASK
    """The current permission mode."""

    read_only: bool = False
    """Deny non-read-only invocations regardless of approval mode."""

    allow_workspace_edits: bool = False
    """Grant safe built-in file edits within configured working directories."""

    interactive: bool = True
    """Whether unresolved approval requests can be sent to a user."""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_mode(cls, data: Any) -> Any:
        """Preserve legacy restrictions and grants when loading state."""
        if not isinstance(data, dict):
            return data
        mode = data.get("mode")
        if mode not in ("default", "accept_edits", "explore", "dont_ask"):
            return data
        data = {**data, "mode": PermissionMode.ASK}
        if mode == "explore":
            data["read_only"] = True
        if mode in ("accept_edits", "dont_ask"):
            data["allow_workspace_edits"] = True
        if mode == "dont_ask":
            data["interactive"] = False
        return data

    working_directories: dict[str, AdditionalWorkingDirectory] = Field(
        default_factory=dict,
    )
    """Additional directories allowed for file operations, keyed by path."""

    allow_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that allow tool execution, keyed by tool name."""

    deny_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that deny tool execution, keyed by tool name."""

    ask_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    """Rules that require approval by the selected mode, keyed by tool name."""

    def restricted_by(self, other: "PermissionContext") -> "PermissionContext":
        """Intersect grants and keep restrictions from both policy sources."""
        updates: dict[str, Any] = {
            "mode": self.mode.restricted_by(other.mode),
            "read_only": self.read_only or other.read_only,
            "interactive": self.interactive and other.interactive,
            "allow_workspace_edits": (
                self.allow_workspace_edits and other.allow_workspace_edits
            ),
            "working_directories": {
                path: value
                for path, value in self.working_directories.items()
                if path in other.working_directories
            },
            "allow_rules": {
                name: [
                    rule
                    for rule in rules
                    if rule in other.allow_rules.get(name, [])
                ]
                for name, rules in self.allow_rules.items()
            },
        }
        for field in ("ask_rules", "deny_rules"):
            merged = {
                name: list(rules)
                for name, rules in getattr(self, field).items()
            }
            for name, rules in getattr(other, field).items():
                current = merged.setdefault(name, [])
                current.extend(rule for rule in rules if rule not in current)
            updates[field] = merged
        return self.model_copy(update=updates)
