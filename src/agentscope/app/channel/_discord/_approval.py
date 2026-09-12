# -*- coding: utf-8 -*-
"""Discord tool-approval button ``custom_id`` helpers.

Approval cards are sent by REST-only channel instances while component
interactions arrive on the gateway-connected client. Explicit
``custom_id`` values on the buttons plus parsing here let the listener
resume the correct run without sharing a :class:`discord.Client` view
store with the sender.
"""
import base64
import json

_PREFIX = "asco:"
# Discord caps ``custom_id`` at 100 characters.
_MAX_CUSTOM_ID_LEN = 100


def _approval_custom_id(
    approved: bool,
    tool_call_id: str,
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build a ``custom_id`` for an approval or deny button.

    Args:
        approved (`bool`): ``True`` for approve, ``False`` for deny.
        tool_call_id (`str`): The awaiting tool call the button answers.
        agent_id (`str`): Target agent pinned at send time.
        session_id (`str`): Target session pinned at send time.

    Returns:
        `str`: A ``custom_id`` within Discord's length limit.

    Raises:
        ValueError: When the payload cannot be encoded within the limit.
    """
    payload = {
        "a": approved,
        "t": tool_call_id,
        "g": agent_id,
        "s": session_id,
    }
    for keys in (("t", "g", "s"), ("t", "s"), ("t",)):
        body = {key: payload[key] for key in keys}
        body["a"] = approved
        encoded = base64.urlsafe_b64encode(
            json.dumps(body, separators=(",", ":")).encode(),
        ).decode().rstrip("=")
        custom_id = f"{_PREFIX}{encoded}"
        if len(custom_id) <= _MAX_CUSTOM_ID_LEN:
            return custom_id
    raise ValueError(
        "Discord approval custom_id exceeds the platform limit",
    )


def _parse_approval_custom_id(
    custom_id: str | None,
) -> tuple[str, bool, str, str] | None:
    """Parse a button ``custom_id`` into approval metadata.

    Args:
        custom_id (`str | None`): The clicked button's ``custom_id``.

    Returns:
        `tuple[str, bool, str, str] | None`:
        ``(tool_call_id, approved, agent_id, session_id)`` for our
        buttons, or ``None`` when the id is missing or not ours.
    """
    if not custom_id or not custom_id.startswith(_PREFIX):
        return None
    encoded = custom_id[len(_PREFIX) :]
    pad = (-len(encoded)) % 4
    try:
        body = json.loads(
            base64.urlsafe_b64decode(encoded + ("=" * pad)).decode(),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    tool_call_id = str(body.get("t") or "").strip()
    if not tool_call_id:
        return None
    approved = bool(body.get("a"))
    agent_id = str(body.get("g") or "").strip()
    session_id = str(body.get("s") or "").strip()
    return tool_call_id, approved, agent_id, session_id
