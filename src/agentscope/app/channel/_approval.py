# -*- coding: utf-8 -*-
"""Channel-owned state for pending tool approvals."""
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
import re
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from pydantic import BaseModel

from ...event import RequireUserConfirmEvent
from ...message import ToolCallState
from ...state import AgentState
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase

_APPROVAL_IDS_KEY = "channel_approval_ids"
CHANNEL_APPROVAL_UNAVAILABLE = "channel_approval_unavailable"
WEB_UI_APPROVAL_NOTICE = (
    "Tool execution needs approval. Open this session in the Web UI "
    "to approve or deny the tool call."
)


class ChannelApprovalRecord(BaseModel):
    """Authoritative routing and authorization data for one approval card."""

    channel_id: str
    chat_id: str
    agent_id: str
    session_id: str
    reply_id: str
    tool_call_id: str
    requester_id: str


def new_approval_id(session_id: str) -> str:
    """Include the lookup namespace and a random nonce in an approval token."""
    if not session_id:
        raise ValueError("An approval requires a session id")
    return f"{quote(session_id, safe='')}:{uuid4().hex}"


def _approval_session_id(approval_id: str) -> str:
    """Decode a session token, rejecting malformed or ambiguous encodings."""
    encoded, nonce = approval_id.split(":")
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("Invalid approval token")
    session_id = unquote(encoded, errors="strict")
    if not session_id or quote(session_id, safe="") != encoded:
        raise ValueError("Invalid approval session encoding")
    return session_id


def with_approval_ids(
    raw: dict[str, Any],
    approval_ids: dict[str, str],
) -> dict[str, Any]:
    """Return an event payload carrying tool-call approval ids."""
    enriched = dict(raw)
    enriched["metadata"] = {
        **dict(raw.get("metadata") or {}),
        _APPROVAL_IDS_KEY: approval_ids,
    }
    return enriched


def approval_id_for(
    request: RequireUserConfirmEvent,
    tool_call_id: str,
) -> str:
    """Read one tool call's opaque approval id from event metadata."""
    approval_ids = request.metadata.get(_APPROVAL_IDS_KEY)
    if not isinstance(approval_ids, dict):
        return ""
    approval_id = approval_ids.get(tool_call_id)
    return approval_id if isinstance(approval_id, str) else ""


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
        MessageBusKeys.channel_session_requesters(session_id),
        reply_id,
        requester_id,
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
            MessageBusKeys.channel_session_requesters(session_id),
            reply_id,
        )
        or ""
    )


async def forget_reply_requester(
    bus: MessageBus,
    session_id: str,
    reply_id: str,
) -> None:
    """Remove the requester of a completed reply."""
    await bus.registry_del(
        MessageBusKeys.channel_session_requesters(session_id),
        reply_id,
    )


async def store_approval(
    bus: MessageBus,
    approval_id: str,
    record: ChannelApprovalRecord,
) -> None:
    """Store the complete approval once in its session namespace."""
    if _approval_session_id(approval_id) != record.session_id:
        raise ValueError("Approval token does not match the record session")
    await bus.registry_set(
        MessageBusKeys.channel_session_approvals(record.session_id),
        approval_id,
        record.model_dump_json(),
    )


async def load_approval(
    bus: MessageBus,
    approval_id: str,
) -> ChannelApprovalRecord | None:
    """Load an approval using the session encoded in its token."""
    try:
        session_id = _approval_session_id(approval_id)
    except (ValueError, UnicodeError):
        return None
    raw = await bus.registry_get(
        MessageBusKeys.channel_session_approvals(session_id),
        approval_id,
    )
    if not raw:
        return None
    record = ChannelApprovalRecord.model_validate_json(raw)
    return record if record.session_id == session_id else None


async def forget_approval(bus: MessageBus, approval_id: str) -> None:
    """Remove a single approval using the namespace carried in its token."""
    try:
        session_id = _approval_session_id(approval_id)
    except (ValueError, UnicodeError):
        return
    await bus.registry_del(
        MessageBusKeys.channel_session_approvals(session_id),
        approval_id,
    )


@asynccontextmanager
async def approval_session(
    bus: MessageBus,
    storage: StorageBase,
    *,
    channel_id: str,
    agent_id: str,
    session_id: str,
) -> AsyncIterator[tuple[AgentState, str] | None]:
    """Serialize approval creation with run persistence and session deletion."""
    async with bus.acquire_lock(MessageBusKeys.session_lock(session_id)):
        channel = await storage.get_channel(channel_id)
        if channel is None:
            yield None
            return
        session = await storage.get_session(
            user_id=channel.user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        agent = await storage.get_agent(
            user_id=channel.user_id,
            agent_id=agent_id,
        )
        yield (session.state, agent.data.name) if session and agent else None


async def prune_session_approvals(
    bus: MessageBus,
    session_id: str,
    state: AgentState,
    agent_name: str,
    *,
    completed_reply_ids: Iterable[str] = (),
) -> None:
    """Clean up saved channel replies while the caller holds the session lock."""
    # Completion comes from persisted REPLY_END messages, independently of
    # whether a platform delivery consumed the end event.
    for reply_id in completed_reply_ids:
        await forget_reply_requester(bus, session_id, reply_id)
    namespace = MessageBusKeys.channel_session_approvals(session_id)
    pending = {
        call.id
        for call in state.get_awaiting_tool_calls(agent_name)
        if call.state == ToolCallState.ASKING
    }
    for approval_id, raw in (await bus.registry_getall(namespace)).items():
        record = ChannelApprovalRecord.model_validate_json(raw)
        if (
            record.reply_id != state.reply_id
            or record.tool_call_id not in pending
        ):
            await bus.registry_del(namespace, approval_id)


async def purge_session_approvals(bus: MessageBus, session_id: str) -> None:
    """Drop the session's approval and requester namespaces under its lock."""
    await bus.registry_drop(
        MessageBusKeys.channel_session_approvals(session_id)
    )
    await bus.registry_drop(
        MessageBusKeys.channel_session_requesters(session_id),
    )
