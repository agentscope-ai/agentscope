# -*- coding: utf-8 -*-
"""Original, source-tagged instructions retained for permission review."""
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..message import ContentBlock, Msg


_Source = Literal["user", "system", "steering_user"]


class AuthorizationEvidence(BaseModel):
    """An original instruction, never a model-generated summary."""

    source: _Source
    message_id: str
    block_id: str
    author: str
    created_at: str
    text: str


class ScheduleAuthorization(BaseModel):
    """Authorization scoped to one current scheduled task."""

    schedule_id: str
    user_id: str
    source: Literal["USER", "AGENT"]
    description: str
    authorized_description: str | None = None
    evidence: list[AuthorizationEvidence] = Field(default_factory=list)
    complete: bool = True


def has_verified_source(message: "Msg") -> bool:
    """Generated and ambiguous legacy messages cannot grant permission."""
    return message.metadata.get("authorization_source") not in (
        "generated",
        "unverified",
    )


def contains_authorization_media(
    message: "Msg",
    block: "ContentBlock",
) -> bool:
    """Identify media supplied as original user or system instructions."""
    if not has_verified_source(message):
        return False
    if block.type == "data":
        return message.role in ("user", "system")
    return (
        block.type == "hint"
        and block.source_role in ("user", "system")
        and isinstance(block.hint, list)
        and any(item.type == "data" for item in block.hint)
    )


def has_unreviewable_input(messages: list["Msg"]) -> bool:
    """Detect original instructions with media a text reviewer cannot see."""
    return any(
        message.metadata.get("authorization_source") == "unverified"
        or any(
            contains_authorization_media(message, b) for b in message.content
        )
        for message in messages
    )


def authorization_key(
    message: "Msg",
    block: "ContentBlock",
) -> tuple[str, str, _Source, str] | None:
    """Identify the same original instruction in history and its archive."""
    if not has_verified_source(message):
        return None
    source: _Source
    if message.role in ("user", "system") and block.type == "text":
        source, text = message.role, block.text
    elif block.type == "hint" and block.source_role in ("user", "system"):
        source = "system" if block.source_role == "system" else "steering_user"
        text = (
            block.hint
            if isinstance(block.hint, str)
            else "\n".join(
                item.text for item in block.hint if item.type == "text"
            )
        )
    else:
        return None
    return (message.id, block.id, source, text) if text else None


def retain_authorization_evidence(
    existing: list[AuthorizationEvidence],
    messages: list["Msg"],
) -> list[AuthorizationEvidence]:
    """Retain original text in order, without promoting untrusted hints."""
    unique = {
        (item.message_id, item.block_id, item.source, item.text): item
        for item in existing
    }
    for message in messages:
        for block in message.content:
            key = authorization_key(message, block)
            if key is not None and key not in unique:
                message_id, block_id, source, text = key
                unique[key] = AuthorizationEvidence(
                    source=source,
                    message_id=message_id,
                    block_id=block_id,
                    author=message.name,
                    created_at=block.created_at,
                    text=text,
                )
    return list(unique.values())
