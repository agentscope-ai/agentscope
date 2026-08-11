# -*- coding: utf-8 -*-
"""DingTalk interactive-card helpers for tool approval.

DingTalk cards use a template created in the Card Platform. The template
round-trips the lookup keys defined here as callback parameters. Session
state remains authoritative; none of the tool input is trusted on callback.
"""

import json
from dataclasses import dataclass
from typing import Any

_APPROVE_ACTIONS = frozenset({"allow", "approve", "accept", "agree"})
_DENY_ACTIONS = frozenset({"deny", "reject"})


@dataclass(frozen=True, slots=True)
class _ApprovalDecision:
    """A validated decision parsed from a DingTalk card callback."""

    out_track_id: str
    user_id: str
    approver_id: str
    tool_call_id: str
    chat_id: str
    agent_id: str
    session_id: str
    approved: bool


def _approval_card_data(
    tool_call_id: str,
    chat_id: str,
    tool_name: str,
    summary: str,
    approver_id: str,
    agent_id: str = "",
    session_id: str = "",
) -> dict[str, str]:
    """Build the parameter map consumed by the configured card template.

    Args:
        tool_call_id (`str`): Awaiting tool call answered by the card.
        chat_id (`str`): Encoded DingTalk chat used for session routing.
        tool_name (`str`): Tool name displayed to the user.
        summary (`str`): Truncated tool arguments displayed to the user.
        approver_id (`str`): Optional user permitted to decide the request.
        agent_id (`str`): Resolved agent id, echoed through the callback.
        session_id (`str`): Resolved session id, echoed through the callback.

    Returns:
        `dict[str, str]`: DingTalk card template parameter map.
    """
    shown = summary if len(summary) <= 800 else summary[:799] + "…"
    return {
        "title": "Tool execution needs approval",
        "markdown": f"**Tool:** `{tool_name}`\n**Arguments:** {shown}",
        "status": "pending",
        "toolCallId": tool_call_id,
        "chatId": chat_id,
        "agentId": agent_id,
        "sessionId": session_id,
        "approverId": approver_id,
    }


def _resolved_card_data(approved: bool) -> dict[str, str]:
    """Build card parameters used after a decision.

    Args:
        approved (`bool`): Whether the tool call was approved.

    Returns:
        `dict[str, str]`: Replacement values for the card template.
    """
    return {
        "title": "Tool execution approved" if approved else "Tool denied",
        "markdown": (
            "The tool was approved and will continue."
            if approved
            else "The tool was denied."
        ),
        "status": "approved" if approved else "denied",
    }


def _parse_card_callback(payload: Any) -> _ApprovalDecision | None:
    """Parse and validate one advanced-card action callback.

    The configured allow and deny buttons must return ``action`` plus the
    routing fields from :func:`_approval_card_data` in
    ``cardPrivateData.params``.

    Args:
        payload (`Any`): Callback data supplied by the Stream SDK.

    Returns:
        `_ApprovalDecision | None`: Parsed decision, or ``None`` for a
        malformed or unrelated callback.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("type") not in (None, "", "actionCallback"):
        return None
    content = _json_object(payload.get("content"))
    private_data = _json_object(content.get("cardPrivateData"))
    params = _json_object(private_data.get("params"))

    action = str(params.get("action") or "").strip().lower()
    if action in _APPROVE_ACTIONS:
        approved = True
    elif action in _DENY_ACTIONS:
        approved = False
    else:
        return None

    tool_call_id = _field(params, "toolCallId", "tool_call_id")
    chat_id = _field(params, "chatId", "chat_id")
    approver_id = _field(params, "approverId", "approver_id")
    user_id = _field(payload, "userId", "user_id")
    out_track_id = _field(payload, "outTrackId", "out_track_id")
    if not all((tool_call_id, chat_id, user_id, out_track_id)):
        return None
    return _ApprovalDecision(
        out_track_id=out_track_id,
        user_id=user_id,
        approver_id=approver_id,
        tool_call_id=tool_call_id,
        chat_id=chat_id,
        agent_id=_field(params, "agentId", "agent_id"),
        session_id=_field(params, "sessionId", "session_id"),
        approved=approved,
    )


def _json_object(value: Any) -> dict[str, Any]:
    """Return a mapping from a mapping or JSON object string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _field(mapping: dict[str, Any], *names: str) -> str:
    """Read the first non-empty string representation of named fields."""
    for name in names:
        value = str(mapping.get(name) or "").strip()
        if value:
            return value
    return ""
