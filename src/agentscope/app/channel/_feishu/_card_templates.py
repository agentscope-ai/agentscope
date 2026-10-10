# -*- coding: utf-8 -*-
"""Feishu interactive-card helpers for the tool-approval flow."""
import json
from typing import Any

_ACTION_TYPE = "tool_guard_approval"
_APPROVE = "approve"
_DENY = "deny"


def _build_approval_card(
    chat_id: str,
    tool_name: str,
    summary: str,
    approval_id: str = "",
) -> str:
    """Build the approval card (JSON string) for a pending tool call.

    Args:
        chat_id (`str`): Chat the card is sent to, echoed on click for
            approval-origin validation.
        tool_name (`str`): Name of the tool, shown in the card body.
        summary (`str`): A rendering of the tool arguments (truncated).
        approval_id (`str`): Opaque key for authoritative server-side state.

    Returns:
        `str`: The card as a JSON string.
    """
    base = {
        "type": _ACTION_TYPE,
        "chat_id": chat_id,
    }
    if approval_id:
        base["approval_id"] = approval_id
    body = f"**Tool:** `{tool_name}`"
    if summary:
        shown = summary if len(summary) <= 800 else summary[:799] + "…"
        body += f"\n**Arguments:** {shown}"
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "🛡️ Tool execution needs approval",
            },
        },
        "elements": [
            {"tag": "markdown", "content": body},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ Allow"},
                        "type": "primary",
                        "value": {**base, "action": _APPROVE},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "❌ Deny"},
                        "type": "danger",
                        "value": {**base, "action": _DENY},
                    },
                ],
            },
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def _resolved_card(approved: bool) -> dict:
    """Build the post-decision card object that replaces the approval card.

    Args:
        approved (`bool`): The decision, selecting the colour and text.

    Returns:
        `dict`: The card object (schema 1.0).
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if approved else "red",
            "title": {
                "tag": "plain_text",
                "content": "✅ Allowed" if approved else "🚫 Denied",
            },
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "The tool was allowed to run."
                    if approved
                    else "The tool was denied."
                ),
            },
        ],
    }


def _parse_action(
    value: Any,
) -> tuple[str, bool, str] | None:
    """Parse a card button's value and its server-side approval id.

    Args:
        value (`Any`): The clicked button's ``value`` — a dict (or JSON
            string) carrying ``type`` / ``chat_id`` / ``action`` /
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


def _build_notice_toast(content: str) -> Any:
    """Build a warning toast without replacing the shared card."""
    return _wrap_response(
        {"toast": {"type": "warning", "content": content}},
    )


def _build_toast(approved: bool) -> Any:
    """Build a toast-only card-callback response (no card update).

    Used for clicks we cannot act on (e.g. an unparseable button), where
    the card should stay as-is.

    Args:
        approved (`bool`):
            The decision, selecting the toast style and text.

    Returns:
        `Any`:
            A ``P2CardActionTriggerResponse`` when lark_oapi is
            importable, else a plain dict with the same shape.
    """
    return _wrap_response({"toast": _toast(approved)})


def _build_action_response(approved: bool) -> Any:
    """Build the card-callback response for a decision: a toast plus the
    resolved card, so Feishu updates the clicked card in place — no
    separate, rate-limit-racing PATCH.

    Args:
        approved (`bool`): The decision, selecting toast and card.

    Returns:
        `Any`: A ``P2CardActionTriggerResponse`` (or dict fallback).
    """
    return _wrap_response(
        {
            "toast": _toast(approved),
            "card": {"type": "raw", "data": _resolved_card(approved)},
        },
    )


def _toast(approved: bool) -> dict:
    """The toast payload for a decision.

    Args:
        approved (`bool`): The decision, selecting style and text.
    """
    return {
        "type": "success" if approved else "info",
        "content": "Allowed" if approved else "Denied",
    }


def _wrap_response(body: dict) -> Any:
    """Wrap a response body in ``P2CardActionTriggerResponse`` when the
    SDK is importable, else return the plain dict.

    Args:
        body (`dict`): The callback response body (``toast`` / ``card``).
    """
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        return P2CardActionTriggerResponse(body)
    except (ImportError, AttributeError):
        return body
