# -*- coding: utf-8 -*-
"""DingTalk interactive-card helpers for tool approval.

DingTalk cards use a template created in the Card Platform. The template
round-trips the lookup keys defined here as callback parameters. Session
state remains authoritative; none of the tool input is trusted on callback.
"""

import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from ....message import ToolCallBlock

# A template author reaches for the same word the card's own status uses,
# so the settled spellings are accepted alongside the imperative ones.
_APPROVE_ACTIONS = frozenset(
    {"allow", "approve", "approved", "accept", "agree"},
)
_DENY_ACTIONS = frozenset({"deny", "denied", "reject"})

# A card's tracking id has to be unique and a tool call id is
# not: the Ollama adapter names the first call of every response
# ``0_<tool>``. Prefix a fresh one, so the tracking id is unique
# and the call it answers is still readable off the end of it.
# The prefix is fixed-width hex, so it needs no separator and
# introduces no character the id did not already allow.
_TRACK_PREFIX_LEN = 32

# DingTalk's general AI card renders the components its layout names and
# takes its buttons with them, so an approval card needs no template of
# the operator's own. A button reports its ``id`` when clicked.
_PENDING_LAYOUT = json.dumps(
    {
        "order": ["msgTitle", "staticMsgContent", "msgButtons"],
        "msgButtons": [
            {
                "text": "✅ 同意",
                "color": "blue",
                "id": "agree",
                "request": True,
            },
            {
                "text": "🚫 拒绝",
                "color": "gray",
                "id": "reject",
                "request": True,
            },
        ],
    },
    ensure_ascii=False,
)
_SETTLED_LAYOUT = json.dumps({"order": ["msgTitle", "staticMsgContent"]})
_PENDING_TITLE = "工具审批"
# DingTalk caps one cardParamMap value at 1KB, so this bounds what a
# card is created and updated with. It says nothing about the streaming
# endpoint, which documents no limit of its own.
_PARAM_VALUE_BUDGET = 900


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


def _tracking_id(tool_call_id: str) -> str:
    """Build the card tracking id that answers ``tool_call_id``.

    Args:
        tool_call_id (`str`): The tool call the card asks about.

    Returns:
        `str`: A tracking id unique to this card.
    """
    return uuid4().hex + tool_call_id


def _tool_call_id(track: str) -> str:
    """Read back the tool call a tracking id was built for.

    Args:
        track (`str`): The ``outTrackId`` a callback reported.

    Returns:
        `str`: The tool call id, or ``""`` when the id is not one of ours.
    """
    return track[_TRACK_PREFIX_LEN:]


def _approval_card_data(
    tool: "ToolCallBlock",
    agent_name: str,
) -> dict[str, str]:
    """Build the parameter map consumed by the configured card template.

    A template of the operator's own binds the tool call under the field
    names it already has — ``name``, ``input`` and ``created_at`` — so
    authoring one needs no vocabulary beyond the block being approved.
    ``status`` is the card's own lifecycle, which the block does not
    carry: its own state stays ``asking`` throughout.

    Args:
        tool (`ToolCallBlock`): The tool call awaiting a decision.
        agent_name (`str`): The agent that asked, for the ready-made
            ``title``; the built-in card does not show it.

    Returns:
        `dict[str, str]`: DingTalk card template parameter map.
    """
    # A card parameter value is capped at 1KB, which a Chinese argument
    # reaches in a third of the characters a counted trim would allow.
    # Nothing else here is caller-sized.
    encoded = tool.input.encode("utf-8")
    shown = encoded[:_PARAM_VALUE_BUDGET].decode("utf-8", "ignore")
    if len(encoded) > _PARAM_VALUE_BUDGET:
        shown += "…"
    # Markdown reads a lone newline as a space, so the two lines need a
    # blank one between them to stay two lines.
    return {
        # What the built-in AI card renders.
        "msgTitle": _PENDING_TITLE,
        "staticMsgContent": f"工具：{tool.name}\n\n参数：{shown}",
        "sys_full_json_obj": _PENDING_LAYOUT,
        # What a template of the operator's own binds.
        "title": f"{agent_name} 提交的工具执行".strip(),
        "name": tool.name,
        "input": shown,
        "created_at": tool.created_at[:19].replace("T", " "),
        "status": "pending",
    }


def _resolved_card_data(approved: bool) -> dict[str, str]:
    """Build card parameters used after a decision.

    Args:
        approved (`bool`): Whether the tool call was approved.

    Returns:
        `dict[str, str]`: Replacement values for the card template.
    """
    return {
        # Settling drops the buttons: the decision is already made. The
        # flow status has to be repeated — an update that omits it leaves
        # the AI card with nothing to render.
        "msgTitle": _PENDING_TITLE,
        "staticMsgContent": ("✅ 已同意，工具继续执行。" if approved else "🚫 已拒绝。"),
        "sys_full_json_obj": _SETTLED_LAYOUT,
        "flowStatus": "3",
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

    # A button built into the layout reports itself by ``id``; a template
    # authored with an explicit action reports that instead. The sibling
    # ``actionIds`` names the layout node, which identifies nothing.
    action = (
        str(params.get("action") or params.get("id") or "").strip().lower()
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
    # carry it. Otherwise the tracking id names the tool call it was
    # created for, and the callback says which chat it came from.
    tool_call_id = _field(
        params,
        "toolCallId",
        "tool_call_id",
    ) or _tool_call_id(out_track_id)
    chat_id = _field(params, "chatId", "chat_id") or _chat_from_space(
        payload,
        user_id,
    )
    approver_id = _field(params, "approverId", "approver_id")
    if not tool_call_id or not chat_id:
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
    if "GROUP" in space_type and space_id:
        return f"group:{space_id}"
    if "ROBOT" in space_type and user_id:
        return f"user:{user_id}"
    # A plain "im" space names the conversation the card sits in — which
    # in a one-to-one chat is the person who clicked.
    if space_id and space_id != user_id:
        return f"group:{space_id}"
    return f"user:{user_id}" if user_id else ""


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
