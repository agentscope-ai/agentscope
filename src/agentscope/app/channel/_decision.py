# -*- coding: utf-8 -*-
"""Validate and atomically resume a tool approval.

The awaiting confirmation is always read from session state. A short-lived
server-side approval record may pin the expected reply, but never replaces
the session as the source of truth for the tool call itself.
"""
from ...event import ConfirmResult, UserConfirmResultEvent
from ...message import ToolCallBlock, ToolCallState
from .._bus_ops import enqueue_run_trigger
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase


async def _load_awaiting(
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> tuple[list[ToolCallBlock], str]:
    """Load a session's ASKING tool calls and its current reply id.

    Args:
        storage (`StorageBase`): Application storage.
        user_id (`str`): Owner of the session.
        agent_id (`str`): The routed agent.
        session_id (`str`): The derived session.

    Returns:
        `tuple[list[ToolCallBlock], str]`: The tool calls awaiting user
        confirmation, and ``state.reply_id`` (empty when absent).
    """
    session = await storage.get_session(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    agent = await storage.get_agent(user_id=user_id, agent_id=agent_id)
    if session is None or agent is None:
        return [], ""
    asking = [
        tc
        for tc in session.state.get_awaiting_tool_calls(agent.data.name)
        if tc.state == ToolCallState.ASKING
    ]
    return asking, session.state.reply_id


async def resume_after_decision(
    bus: MessageBus,
    storage: StorageBase,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    tool_call_id: str,
    approved: bool,
    expected_reply_id: str = "",
    approval_id: str = "",
) -> bool:
    """Resume the run with a decision on ``tool_call_id``.

    Reads the authoritative tool call from session state (ignoring the
    caller's round-tripped copy); returns ``False`` — a no-op — when it
    is not currently ASKING (stale/already resolved).

    Args:
        bus (`MessageBus`): The application message bus.
        storage (`StorageBase`): Application storage.
        user_id (`str`): Owner of the session.
        agent_id (`str`): The routed agent.
        session_id (`str`): The derived session.
        tool_call_id (`str`): The awaiting tool call to answer.
        approved (`bool`): The user's decision.
        expected_reply_id (`str`): Reply pinned by the approval record.
        approval_id (`str`): Approval round used to make the claim one-shot.

    Returns:
        `bool`: Whether a resume was enqueued.
    """
    asking, reply_id = await _load_awaiting(
        storage,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    if expected_reply_id and reply_id != expected_reply_id:
        return False
    tool_call = next((t for t in asking if t.id == tool_call_id), None)
    if tool_call is None:
        return False
    claim_key = MessageBusKeys.channel_approval_claim(
        session_id,
        reply_id,
        tool_call_id,
        approval_id,
    )
    if not await bus.try_lock(
        claim_key,
        ttl_secs=MessageBusKeys.CHANNEL_APPROVAL_TTL_SECS,
    ):
        return False
    try:
        await enqueue_run_trigger(
            bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=UserConfirmResultEvent(
                reply_id=reply_id,
                confirm_results=[
                    ConfirmResult(confirmed=approved, tool_call=tool_call),
                ],
            ),
        )
        return True
    except Exception:
        await bus.unlock(claim_key)
        raise
