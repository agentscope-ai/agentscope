# -*- coding: utf-8 -*-
"""Slack Block Kit helpers for the tool-approval flow.

A button's ``value`` holds up to 2000 characters, so the card round-trips
the lookup keys (``tool_call_id``, ``chat_id`` and the resolved
``agent_id`` / ``session_id``) exactly as the Feishu card does — no
in-process table, which keeps clicks working whichever node the app's
Socket Mode connection lands on. The authoritative tool call is still
read from session state on resume, never trusted from the card.
"""
import json
from typing import Any

_ACTION_TYPE = "tool_guard_approval"
_APPROVE = "approve"
_DENY = "deny"
# Slack rejects a section longer than this.
_SECTION_LIMIT = 3000
# ...and a button value longer than this.
_VALUE_LIMIT = 2000


def _build_approval_blocks(
    tool_call_id: str,
    chat_id: str,
    tool_name: str,
    summary: str,
    agent_id: str = "",
    session_id: str = "",
) -> list[dict]:
    """Build the approval message's blocks for a pending tool call.

    Args:
        tool_call_id (`str`): The awaiting tool call the buttons answer.
        chat_id (`str`): Channel the card is sent to, echoed on click for
            session routing.
        tool_name (`str`): Name of the tool, shown in the card body.
        summary (`str`): A rendering of the tool arguments (truncated).
        agent_id (`str`): Target agent, echoed on click to resume the
            exact run without re-resolving routing.
        session_id (`str`): Target session, echoed on click alongside
            ``agent_id``.

    Returns:
        `list[dict]`: The Block Kit blocks to post.
    """
    base = {
        "type": _ACTION_TYPE,
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "session_id": session_id,
    }
    body = f"*Tool:* `{tool_name}`"
    if summary:
        shown = summary if len(summary) <= 800 else summary[:799] + "…"
        body += f"\n*Arguments:* {shown}"
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🛡️ Tool execution needs approval",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": body[:_SECTION_LIMIT],
            },
        },
        {
            "type": "actions",
            "elements": [
                _button("✅ Allow", "primary", {**base, "action": _APPROVE}),
                _button("❌ Deny", "danger", {**base, "action": _DENY}),
            ],
        },
    ]


def _button(label: str, style: str, value: dict) -> dict:
    """Build one action button carrying ``value`` as its payload.

    Args:
        label (`str`): The button's visible text.
        style (`str`): Slack button style (``primary`` / ``danger``).
        value (`dict`): The payload echoed back on click.
    """
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "style": style,
        "action_id": f"{_ACTION_TYPE}:{value['action']}",
        "value": json.dumps(value, ensure_ascii=False)[:_VALUE_LIMIT],
    }


def _resolved_blocks(approved: bool) -> list[dict]:
    """Build the blocks that replace the approval card after a decision.

    Args:
        approved (`bool`): The decision, selecting the wording.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "✅ *Allowed* — the tool was allowed to run."
                    if approved
                    else "🚫 *Denied* — the tool was denied."
                ),
            },
        },
    ]


def _parse_action(value: Any) -> tuple[str, str, bool, str, str] | None:
    """Parse a clicked button's ``value`` into ``(tool_call_id, chat_id,
    approved, agent_id, session_id)``.

    Args:
        value (`Any`): The clicked button's ``value`` — a JSON string (or
            dict) carrying ``type`` / ``tool_call_id`` / ``chat_id`` /
            ``action`` / ``agent_id`` / ``session_id``.

    Returns:
        `tuple[str, str, bool, str, str] | None`: The decision keys for a
        valid button, or ``None`` if not one of ours.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or value.get("type") != _ACTION_TYPE:
        return None
    tool_call_id = str(value.get("tool_call_id") or "").strip()
    chat_id = str(value.get("chat_id") or "").strip()
    action = str(value.get("action") or "").strip().lower()
    if not tool_call_id or action not in (_APPROVE, _DENY):
        return None
    agent_id = str(value.get("agent_id") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    return tool_call_id, chat_id, action == _APPROVE, agent_id, session_id
