# -*- coding: utf-8 -*-
"""Compact Discord component ids for server-backed tool approvals."""

_PREFIX = "asco:approval:"
_MAX_CUSTOM_ID_LEN = 100


def _approval_custom_id(approval_id: str, approved: bool) -> str:
    """Encode an opaque approval id and decision within Discord's limit."""
    action = "a" if approved else "d"
    custom_id = f"{_PREFIX}{action}:{approval_id}"
    if len(custom_id) > _MAX_CUSTOM_ID_LEN:
        raise ValueError("Discord approval custom_id exceeds 100 characters")
    return custom_id


def _parse_approval_custom_id(custom_id: object) -> tuple[str, bool] | None:
    """Decode an AgentScope approval component id, if applicable."""
    if not isinstance(custom_id, str) or not custom_id.startswith(_PREFIX):
        return None
    action, separator, approval_id = custom_id[len(_PREFIX) :].partition(":")
    if not separator or not approval_id or action not in ("a", "d"):
        return None
    return approval_id, action == "a"
