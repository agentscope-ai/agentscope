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

# DingTalk's general AI card renders the components its layout names and
# takes its buttons with them, so an approval card needs no template of
# the operator's own. A button reports its ``id`` when clicked.
_PENDING_LAYOUT = json.dumps(
    {
        "order": ["msgTitle", "staticMsgContent", "msgButtons"],
        "msgButtons": [
            {
                "text": "Approve",
                "color": "blue",
                "id": "agree",
                "request": True,
            },
            {
                "text": "Deny",
                "color": "red",
                "id": "reject",
                "request": True,
            },
        ],
    },
)
_SETTLED_LAYOUT = json.dumps({"order": ["msgTitle", "staticMsgContent"]})


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
    title = "Tool execution needs approval"
    markdown = f"**Tool:** `{tool_name}`\n\n**Arguments:** {shown}"
    return {
        # What the default AI card reads.
        "msgTitle": title,
        "staticMsgContent": markdown,
        "sys_full_json_obj": _PENDING_LAYOUT,
        # What a template of the operator's own binds instead. Routing is
        # recovered from the callback when a template carries none.
        "title": title,
        "markdown": markdown,
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
    title = "Tool execution approved" if approved else "Tool denied"
    markdown = (
        "The tool was approved and will continue."
        if approved
        else "The tool was denied."
    )
    return {
        # Settling drops the buttons: the decision is already made.
        "msgTitle": title,
        "staticMsgContent": markdown,
        "sys_full_json_obj": _SETTLED_LAYOUT,
        "title": title,
        "markdown": markdown,
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
    # Deliberately no check on ``type``: the official SDK never reads it,
    # so its wire values are undocumented and gating on them drops every
    # callback the moment DingTalk sends one we did not guess.
    content = _json_object(payload.get("content"))
    private_data = _json_object(content.get("cardPrivateData"))
    params = _json_object(private_data.get("params"))
    action_ids = private_data.get("actionIds")

    # A template that declares nothing but its buttons reports the click
    # as an action id, so accept either shape.
    action = (
        str(
            params.get("action")
            or (
                action_ids[0]
                if isinstance(action_ids, list) and action_ids
                else ""
            )
            or "",
        )
        .strip()
        .lower()
    )
    if action in _APPROVE_ACTIONS:
        approved = True
    elif action in _DENY_ACTIONS:
        approved = False
    else:
        return None

    user_id = _field(payload, "userId", "user_id")
    out_track_id = _field(payload, "outTrackId", "out_track_id")
    if not all((user_id, out_track_id)):
        return None
    # Routing rides on the template only when the template was built to
    # carry it. Otherwise the tracking id is the tool call it was created
    # for, and the callback says which chat it came from.
    tool_call_id = _field(params, "toolCallId", "tool_call_id") or out_track_id
    chat_id = _field(params, "chatId", "chat_id") or _chat_from_space(
        payload,
        user_id,
    )
    approver_id = _field(params, "approverId", "approver_id")
    if not chat_id:
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


def _chat_from_space(payload: dict[str, Any], user_id: str) -> str:
    """Recover the chat a card was delivered into from its callback.

    Args:
        payload (`dict[str, Any]`): Callback data supplied by the SDK.
        user_id (`str`): The staff id of whoever clicked.

    Returns:
        `str`: Encoded chat, or ``""`` when the space is unrecognised.
    """
    space_type = _field(payload, "spaceType", "space_type").upper()
    space_id = _field(payload, "spaceId", "space_id")
    if space_type == "IM_GROUP" and space_id:
        return f"group:{space_id}"
    if space_type == "IM_ROBOT" and user_id:
        return f"user:{user_id}"
    return ""


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
