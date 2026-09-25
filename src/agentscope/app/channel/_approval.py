# -*- coding: utf-8 -*-
"""Channel-owned state for pending tool approvals."""
from uuid import uuid4

from pydantic import BaseModel

from ..message_bus import MessageBus, MessageBusKeys

_APPROVAL_FIELD = "record"
_REQUESTER_FIELD = "requester"
_APPROVAL_TTL_SECS = MessageBusKeys.CHANNEL_APPROVAL_TTL_SECS
_REPLY_REQUESTER_TTL_SECS = MessageBusKeys.CHANNEL_APPROVAL_TTL_SECS


class ChannelApprovalRecord(BaseModel):
    """Authoritative routing and authorization data for one approval card."""

    channel_id: str
    chat_id: str
    agent_id: str
    session_id: str
    reply_id: str
    tool_call_id: str
    requester_id: str


def new_approval_id() -> str:
    """Return a cryptographically random, opaque approval token."""
    return uuid4().hex


async def remember_reply_requester(
    bus: MessageBus,
    *,
    session_id: str,
    reply_id: str,
    requester_id: str,
) -> None:
    """Remember the user who started a channel reply across continuations."""
    if not requester_id:
        return
    await bus.registry_set(
        MessageBusKeys.channel_reply_requester(session_id, reply_id),
        _REQUESTER_FIELD,
        requester_id,
        ttl_secs=_REPLY_REQUESTER_TTL_SECS,
    )


async def load_reply_requester(
    bus: MessageBus,
    *,
    session_id: str,
    reply_id: str,
) -> str:
    """Load the originating user for a channel reply, if still known."""
    return (
        await bus.registry_get(
            MessageBusKeys.channel_reply_requester(session_id, reply_id),
            _REQUESTER_FIELD,
        )
        or ""
    )


async def store_approval(
    bus: MessageBus,
    approval_id: str,
    record: ChannelApprovalRecord,
) -> None:
    """Persist one pending approval under its opaque platform token."""
    await bus.registry_set(
        MessageBusKeys.channel_approval(approval_id),
        _APPROVAL_FIELD,
        record.model_dump_json(),
        ttl_secs=_APPROVAL_TTL_SECS,
    )


async def load_approval(
    bus: MessageBus,
    approval_id: str,
) -> ChannelApprovalRecord | None:
    """Load one pending approval without consuming it."""
    raw = await bus.registry_get(
        MessageBusKeys.channel_approval(approval_id),
        _APPROVAL_FIELD,
    )
    return ChannelApprovalRecord.model_validate_json(raw) if raw else None


async def forget_approval(bus: MessageBus, approval_id: str) -> None:
    """Remove an approval after its decision has been accepted."""
    await bus.registry_del(
        MessageBusKeys.channel_approval(approval_id),
        _APPROVAL_FIELD,
    )
