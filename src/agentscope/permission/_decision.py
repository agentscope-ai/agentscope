# -*- coding: utf-8 -*-
"""The permission decision result."""
from dataclasses import dataclass
from typing import Any

from ._rule import PermissionRule
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
    """Whether this decision is immune to being silenced by allow rules
    ("bypass-immune").

    Only meaningful when :attr:`behavior` is :attr:`PermissionBehavior.ASK`.
    A tool sets this to ``True`` to signal that the operation is
    dangerous enough that **no allow rule** may convert the ASK into an
    ALLOW — the user must explicitly confirm in-the-moment. In
    :attr:`PermissionMode.DONT_ASK` where no user is available, a
    bypass-immune ASK is converted to DENY rather than silently allowed.

    Per-mode handling of a ``bypass_immune=True`` ASK:

    - ``DEFAULT`` / ``ACCEPT_EDITS``: honored — allow rules cannot
      override.
    - ``EXPLORE``: not applicable (the engine resolves EXPLORE via
      :meth:`ToolBase.check_read_only` and does not invoke
      :meth:`ToolBase.check_permissions`).
    - ``BYPASS``: **intentionally ignored** — BYPASS's contract is
      "the user has opted out of safety prompts; only deny / ask
      rules remain as guardrails." Use deny rules in BYPASS to
      enforce specific protections.
    - ``DONT_ASK``: converted to DENY (no user available).

    Default is ``False``: a regular ASK that may be overridden by an
    allow rule in DEFAULT / ACCEPT_EDITS, and is silently allowed by
    BYPASS's fallback. Tools should set this only for genuine safety
    checks (e.g. writes to dangerous paths, ``rm -rf /``, command
    injection patterns) — not for "I'd prefer user input" cases.

    Note: this field is internal metadata for the permission engine.
    Callers handling the decision (agent loop, HITL backend, UI) treat
    a bypass-immune ASK the same as a regular ASK — both prompt the
    user. The distinction only governs whether engine-level rules /
    modes may override it before reaching the caller.
    """
