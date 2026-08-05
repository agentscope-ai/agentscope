# -*- coding: utf-8 -*-
"""SSE streaming chat router — the Runtime layer entry point.

POST /api/chat/run
    Body: { "session_id": "...", "agent_id": "default",
            "input": "user message text" }
    Response: ``text/event-stream`` — SSE envelope dicts

POST /api/chat/stop
    Body: { "session_id": "..." }
    Response: { "stopped": true }

This router wraps the :class:`Runtime` 8-phase orchestrator into
a FastAPI endpoint. It produces a real SSE stream (``text/event-stream``)
that the frontend parses incrementally.

The built-in ``/chat`` endpoint from AgentScope's ``create_app`` uses
a fire-and-forget + message bus pattern. This router provides a
simpler direct-streaming alternative via the Runtime layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

chat_sse_router = APIRouter(prefix="/api/chat", tags=["chat-sse"])


class ChatRunRequest(BaseModel):
    """Request body for the SSE chat endpoint."""

    session_id: str = Field(default="", description="Conversation thread id")
    agent_id: str = Field(default="default", description="Agent to run")
    input: str = Field(default="", description="User message text")


class ChatStopRequest(BaseModel):
    """Request body for the stop endpoint."""

    session_id: str = Field(description="Session to stop")


@chat_sse_router.post("/run", summary="Run agent and stream SSE events")
async def run_chat(
    body: ChatRunRequest,
    request: Request,
) -> StreamingResponse:
    """Trigger a chat run and stream SSE envelopes.

    The response is a ``text/event-stream``. Each ``data:`` line is
    a JSON-encoded envelope dict (response/message/text/tool_call/...).
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return StreamingResponse(
            _error_stream("Runtime not initialized"),
            media_type="text/event-stream",
        )

    # Build a simple request object for the Runtime
    req = _SimpleRequest(
        session_id=body.session_id,
        agent_id=body.agent_id,
        input=body.input,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for envelope in runtime.run(req):
                yield f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("SSE stream error: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@chat_sse_router.post("/stop", summary="Stop an active chat run")
async def stop_chat(
    body: ChatStopRequest,
    request: Request,
) -> dict:
    """Request cancellation of an active run by session id."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return {"stopped": False, "reason": "runtime not initialized"}
    ok = runtime.cancel(body.session_id)
    return {"stopped": ok}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class _SimpleRequest:
    """Minimal request object for Runtime."""

    def __init__(self, session_id: str, agent_id: str, input: str):
        self.session_id = session_id
        self.agent_id = agent_id
        self.input_msgs = [{"role": "user", "content": input}]


async def _error_stream(msg: str) -> AsyncGenerator[str, None]:
    yield f"data: {json.dumps({'error': msg})}\n\n"


__all__ = ["chat_sse_router"]
