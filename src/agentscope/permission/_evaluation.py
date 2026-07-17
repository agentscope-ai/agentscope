# -*- coding: utf-8 -*-
"""Structured permission evaluation result.

Separates the *effective* decision (what the agent will execute) from any
*candidate* decision that a permission mode transformed or suppressed —
e.g. a bypass-immune safety ASK silenced by BYPASS, or an ASK converted to
DENY by DONT_ASK. This lets an observer middleware audit behavior-changing
permission transitions and final decisions, rather than only the final
(potentially information-stripped) decision.
"""
from dataclasses import dataclass
from enum import Enum

from ._types import PermissionMode
from ._decision import PermissionDecision


class PermissionResolution(Enum):
    """How the effective decision was produced from the candidate.

    Kept small and stable on purpose — finer-grained reasons (which rule
    matched, which safety check fired) are carried by
    :attr:`PermissionDecision.decision_reason`.
    """

    DIRECT = "direct"
    """No transformation; the engine returned the candidate as-is."""

    BYPASS_ASK_SUPPRESSED = "bypass_ask_suppressed"
    """BYPASS silenced a tool-emitted ASK (incl. bypass-immune) into ALLOW."""

    ASK_OVERRIDDEN_BY_ALLOW_RULE = "ask_overridden_by_allow_rule"
    """An allow rule changed a tool-emitted ASK into ALLOW."""

    ASK_CONVERTED_TO_DENY = "ask_converted_to_deny"
    """DONT_ASK converted an ASK into DENY (no user available)."""

    USER_CONFIRMED = "user_confirmed"
    """Decision reused a prior user confirmation; engine was skipped."""


@dataclass(frozen=True)
class PermissionEvaluation:
    """Structured result of a permission check.

    Attributes:
        mode: The :class:`PermissionMode` that produced this evaluation.
        effective_decision: The decision the agent will consume / execute.
        candidate_decision: The decision a mode transformed or suppressed.
            ``None`` when no transformation occurred (``resolution=DIRECT``
            or ``USER_CONFIRMED``). Not a full rule-evaluation trace.
        resolution: How ``effective_decision`` relates to
            ``candidate_decision``.
    """

    mode: PermissionMode
    effective_decision: PermissionDecision
    candidate_decision: PermissionDecision | None = None
    resolution: PermissionResolution = PermissionResolution.DIRECT
