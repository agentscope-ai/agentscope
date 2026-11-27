# -*- coding: utf-8 -*-
"""Slack Block Kit helpers for the tool-approval flow.

A button's ``value`` carries the chat id and the opaque approval id, as
the Feishu card does. Routing and authorization live in the server-side
approval record, so a click resolves whichever node the app's Socket Mode
connection lands on.
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
    chat_id: str,
    tool_name: str,
    summary: str,
    approval_id: str,
) -> list[dict]:
    """Build the approval message's blocks for a pending tool call.

    Args:
        chat_id (`str`): Channel the card is sent to, echoed on click for
            approval-origin validation.
        tool_name (`str`): Name of the tool, shown in the card body.
        summary (`str`): A rendering of the tool arguments (truncated).
        approval_id (`str`): Opaque key for authoritative server-side state.

    Returns:
        `list[dict]`: The Block Kit blocks to post.

    Raises:
        `ValueError`: When the keys do not fit a button value.
    """
    base = {
        "type": _ACTION_TYPE,
        "chat_id": chat_id,
        "approval_id": approval_id,
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

    Raises:
        `ValueError`: When the serialized payload exceeds Slack's limit.
            Cutting it would leave JSON that ``_parse_action`` cannot
            read, so the click would be silently ignored.
    """
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) > _VALUE_LIMIT:
        raise ValueError(
            f"button value is {len(serialized)} characters, over Slack's "
            f"{_VALUE_LIMIT}",
        )
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "style": style,
        "action_id": f"{_ACTION_TYPE}:{value['action']}",
        "value": serialized,
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


def _parse_action(value: Any) -> tuple[str, bool, str] | None:
    """Parse a clicked button's ``value`` and its server-side approval id.

    Args:
        value (`Any`): The clicked button's ``value`` — a JSON string (or
            dict) carrying ``type`` / ``chat_id`` / ``action`` /
            ``approval_id``.

    Returns:
        `tuple[str, bool, str] | None`: Chat, decision, and opaque approval
        id for a valid button; otherwise ``None``.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or value.get("type") != _ACTION_TYPE:
        return None
    chat_id = str(value.get("chat_id") or "").strip()
    action = str(value.get("action") or "").strip().lower()
    approval_id = str(value.get("approval_id") or "").strip()
    if not chat_id or not approval_id or action not in (_APPROVE, _DENY):
        return None
    return chat_id, action == _APPROVE, approval_id
