# -*- coding: utf-8 -*-
"""Chat router — fire-and-forget trigger for chat runs.

The endpoint no longer returns an SSE stream. Instead, it kicks off a
chat run as a background task and returns immediately. Events produced
by the run are published to the message bus and delivered to the
frontend via the long-lived ``GET /sessions/{sid}/stream`` SSE
connection provided by the session router.
"""
import asyncio

from fastapi import APIRouter, Depends

from ..deps import (
    get_chat_service,
    get_current_user_id,
)
from ._schema import ChatRequest, ChatTriggerResponse
from .._service import ChatService

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


@chat_router.post(
    "/",
    response_model=ChatTriggerResponse,
    summary="Trigger a chat run (fire-and-forget)",
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatTriggerResponse:
    """Trigger a chat run for the specified session.

    The run executes as a background task. Events produced during the
    run are published to the message bus and delivered to any active
    ``GET /sessions/{session_id}/stream`` SSE subscriber. The caller
    does **not** receive events from this endpoint's response body.

    Accepts the same ``input`` payloads as before:

    - ``Msg`` / ``list[Msg]``: new user message(s).
    - ``UserConfirmResultEvent`` / ``ExternalExecutionResultEvent``:
      resume a paused tool call (human-in-the-loop).
    - ``None``: continue from current state.

    Args:
        request (`ChatRequest`):
            JSON body with ``agent_id``, ``session_id``, and ``input``.
        user_id (`str`):
            Injected user id.
        chat_service (`ChatService`):
            Injected application-wide chat service.

    Returns:
        `ChatTriggerResponse`:
            Confirms the run was scheduled.
    """
    asyncio.create_task(
        chat_service.run(
            user_id=user_id,
            session_id=request.session_id,
            agent_id=request.agent_id,
            input_msg=request.input,
        ),
    )
    return ChatTriggerResponse(
        status="started",
        session_id=request.session_id,
    )
