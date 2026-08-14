# -*- coding: utf-8 -*-
"""Query cumulative token usage for a session.

Endpoint
--------
``GET /sessions/{session_id}/usage?agent_id=xxx&user_id=xxx``

    Returns ``input_tokens``, ``output_tokens`` and ``message_count``
    aggregated across all persisted messages in the session.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request, status

logger = logging.getLogger("bocomadp.session_usage")

session_usage_router = APIRouter(
    prefix="/sessions",
    tags=["session-usage"],
)


@session_usage_router.get(
    "/{session_id}/usage",
    summary="Get cumulative token usage for a session",
)
async def get_session_usage(
    session_id: str,
    agent_id: str = Query(default="default", description="Agent id"),
    user_id: str = Query(default="default", description="User id"),
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    """Sum token usage across all messages in a session.

    Iterates through the session's message list via paginated
    ``list_messages``, accumulating ``usage.input_tokens`` and
    ``usage.output_tokens`` from every :class:`Msg` that has them.
    """
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage backend not available",
        )

    # Check session ownership
    session = await storage.get_session(user_id, agent_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found",
        )

    total_input = 0
    total_output = 0
    message_count = 0
    before: str | None = None
    batch_limit = 200

    while True:
        messages, has_more = await storage.list_messages(
            user_id,
            session_id,
            limit=batch_limit,
            before=before,
        )
        for msg in messages:
            message_count += 1
            u = getattr(msg, "usage", None)
            if u is not None:
                total_input += getattr(u, "input_tokens", 0) or 0
                total_output += getattr(u, "output_tokens", 0) or 0

        if not has_more or not messages:
            break
        # Move cursor to continue pagination
        before = messages[0].id

    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "message_count": message_count,
    }
