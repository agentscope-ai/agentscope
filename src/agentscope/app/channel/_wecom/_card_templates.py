# -*- coding: utf-8 -*-
"""WeCom template-card helpers for the tool-approval flow.

WeCom's button carries a single ``key`` string rather than Feishu's
arbitrary value object, and that key is length-limited, so the card
cannot round-trip the lookup keys the way the Feishu card does. Each
button therefore carries a short opaque token that the channel resolves
against its own pending table — sound here because WeCom keeps exactly
one live long connection per bot, so a click always comes back to the
process that sent the card.
"""

_APPROVE = "a"
_DENY = "d"


def _button_key(token: str, approved: bool) -> str:
    """The ``key`` for one approval button.

    Args:
        token (`str`): The channel's pending-approval token.
        approved (`bool`): Which button this is.
    """
    return f"{token}:{_APPROVE if approved else _DENY}"


def _parse_button_key(key: str) -> tuple[str, bool] | None:
    """Parse a clicked button's ``key`` into ``(token, approved)``.

    Args:
        key (`str`): The ``event_key`` from a template-card event.

    Returns:
        `tuple[str, bool] | None`: The token and decision, or ``None`` if
        the key is not one of ours.
    """
    token, _, action = (key or "").rpartition(":")
    if not token or action not in (_APPROVE, _DENY):
        return None
    return token, action == _APPROVE


def _build_approval_card(
    task_id: str,
    token: str,
    tool_name: str,
    summary: str,
) -> dict:
    """Build the approval card for a pending tool call.

    Args:
        task_id (`str`): Card task id, echoed back on click and reused to
            update the card in place.
        token (`str`): The pending-approval token carried by the buttons.
        tool_name (`str`): Name of the tool, shown on the card.
        summary (`str`): A rendering of the tool arguments (truncated).
    """
    desc = f"Tool: {tool_name}"
    if summary:
        shown = summary if len(summary) <= 800 else summary[:799] + "…"
        desc += f"\nArguments: {shown}"
    return {
        "card_type": "button_interaction",
        "main_title": {
            "title": "Tool execution needs approval",
            "desc": desc,
        },
        "button_list": [
            {
                "text": "Allow",
                "style": 1,
                "key": _button_key(token, True),
            },
            {
                "text": "Deny",
                "style": 2,
                "key": _button_key(token, False),
            },
        ],
        "task_id": task_id,
    }


def _resolved_card(task_id: str, approved: bool) -> dict:
    """Build the card that replaces the approval card after a decision.

    Args:
        task_id (`str`): The clicked card's task id, which the update
            must match.
        approved (`bool`): The decision, selecting the wording.
    """
    return {
        "card_type": "text_notice",
        "main_title": {
            "title": "Allowed" if approved else "Denied",
            "desc": (
                "The tool was allowed to run."
                if approved
                else "The tool was denied."
            ),
        },
        "task_id": task_id,
    }
