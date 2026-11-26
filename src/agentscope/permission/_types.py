# -*- coding: utf-8 -*-
# pylint: disable=line-too-long
"""Permission system types and engine for tool usage control.

This module implements a permission system that controls tool execution based
on configurable rules. The permission system supports different matching
strategies depending on the tool type:

- For Bash tools: rule_content is a substring pattern matched against commands
- For Write/Read tools: rule_content is a glob pattern matched against file
 paths
- For other tools: rule_content uses generic matching logic
"""

from enum import Enum


class PermissionMode(Enum):
    """Select who resolves tool approval requests.

    Rules and capability restrictions apply before mode dispatch.
    ASK requests human approval, AUTO uses an independent reviewer,
    and BYPASS approves requests without review.
    """

    ASK = "ask"
    AUTO = "auto"
    BYPASS = "bypass"

    def restricted_by(self, other: "PermissionMode") -> "PermissionMode":
        """Choose the more restrictive of two approval modes."""
        for mode in (PermissionMode.ASK, PermissionMode.AUTO):
            if mode in (self, other):
                return mode
        return PermissionMode.BYPASS


class PermissionBehavior(Enum):
    """The behavior of permission.

    Attributes:
        ALLOW: Allow the operation
        DENY: Deny the operation
        ASK: Ask the user for permission
        PASSTHROUGH: Let the permission engine continue with rule matching
            (used by tools to defer decision to the engine)
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"
