# -*- coding: utf-8 -*-
"""The permission decision result."""
from dataclasses import dataclass
from typing import Any

from ._rule import PermissionRule
from ._review import PermissionReview
from ._types import PermissionBehavior


@dataclass
class PermissionDecision:
    """Decision result from permission checking.

    Represents the outcome of a permission check, including whether
    the action should be allowed, denied, or require user confirmation.
    """

    behavior: PermissionBehavior
    """The permission behavior decision."""

    message: str
    """Human-readable message describing the decision."""

    decision_reason: str | None = None
    """Optional explanation for why this decision was made."""

    updated_input: dict[str, Any] | None = None
    """Optional modified input data (e.g., sanitized paths)."""

    suggested_rules: list[PermissionRule] | None = None
    """Optional list of suggested permission rules for user to apply."""

    bypass_immune: bool = False
    """Prevent allow rules from suppressing a tool's safety approval.

    ASK sends the request to the user; AUTO sends it to the reviewer;
    BYPASS approves it. This flag is not a hard deny.
    """

    review: PermissionReview | None = None
    """Structured automatic-review evidence, when a reviewer was invoked."""
