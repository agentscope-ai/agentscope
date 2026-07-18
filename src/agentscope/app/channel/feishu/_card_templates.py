# -*- coding: utf-8 -*-
"""Feishu interactive card templates for tool-guard approval flow."""
import json
from typing import Any, Dict, Optional

ACTION_TYPE = "tool_guard_approval"
APPROVE_KEY = "approve"
DENY_KEY = "deny"


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_approval_card(
    *,
    request_id: str,
    tool_name: str,
    tool_input_summary: str = "",
    session_id: str = "",
    agent_id: str = "",
    user_id: str = "",
) -> str:
    """Build an interactive approval card JSON string."""
    approve_value = {
        "type": ACTION_TYPE,
        "action": APPROVE_KEY,
        "request_id": request_id,
        "tool_name": tool_name,
        "session_id": session_id,
        "agent_id": agent_id,
        "user_id": user_id,
    }
    deny_value = {**approve_value, "action": DENY_KEY}

    body_md = f"**工具:** `{tool_name}`"
    if tool_input_summary:
        body_md += f"\n**参数:** {_truncate(tool_input_summary, 800)}"

    card: Dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "🛡️ 工具执行需要确认",
            },
        },
        "elements": [
            {"tag": "markdown", "content": body_md},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": "✅ 允许执行",
                        },
                        "type": "primary",
                        "value": approve_value,
                    },
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": "❌ 拒绝",
                        },
                        "type": "danger",
                        "value": deny_value,
                    },
                ],
            },
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def build_resolved_card(
    *,
    tool_name: str,
    action: str,
) -> str:
    """Build a resolved (post-click) card JSON string."""
    if action == APPROVE_KEY:
        title = "✅ 已允许执行"
        template = "green"
        status_line = f"工具 `{tool_name}` 已被允许执行。"
    elif action == DENY_KEY:
        title = "🚫 已拒绝"
        template = "red"
        status_line = f"工具 `{tool_name}` 已被拒绝执行。"
    else:
        title = "⌛ 已超时"
        template = "grey"
        status_line = f"工具 `{tool_name}` 的审批已超时。"

    card: Dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "markdown", "content": status_line},
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def parse_action_value(
    action_value: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Parse a card action value dict into structured fields."""
    if action_value.get("type") != ACTION_TYPE:
        return None
    action = str(action_value.get("action") or "").strip().lower()
    request_id = str(action_value.get("request_id") or "").strip()
    if not request_id or action not in (APPROVE_KEY, DENY_KEY):
        return None
    return {
        "action": action,
        "request_id": request_id,
        "tool_name": str(action_value.get("tool_name") or ""),
        "session_id": str(action_value.get("session_id") or ""),
        "agent_id": str(action_value.get("agent_id") or ""),
        "user_id": str(action_value.get("user_id") or ""),
    }


def build_toast_response(
    *,
    action: str,
    tool_name: str,
    card_json: str,
) -> Any:
    """Build the card.action.trigger response with toast and card update.

    Returns a dict suitable for lark-oapi's P2CardActionTriggerResponse.
    """
    if action == APPROVE_KEY:
        toast = {"type": "success", "content": f"已允许 {tool_name}"}
    elif action == DENY_KEY:
        toast = {"type": "info", "content": f"已拒绝 {tool_name}"}
    else:
        toast = {"type": "warning", "content": "审批已超时"}

    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        return P2CardActionTriggerResponse(
            {
                "toast": toast,
                "card": {
                    "type": "raw",
                    "data": json.loads(card_json),
                },
            },
        )
    except (ImportError, AttributeError):
        return {
            "toast": toast,
            "card": {"type": "raw", "data": json.loads(card_json)},
        }
