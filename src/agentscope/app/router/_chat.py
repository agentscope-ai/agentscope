# -*- coding: utf-8 -*-
"""Chat router providing a streaming SSE chat endpoint."""
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    session_id: str = Field(
        description="The session to send the message to.",
    )
    message: str = Field(
        description="The user's input message.",
    )


async def _event_generator(
    request: ChatRequest,
) -> AsyncGenerator[str, None]:
    """Async generator that drives the agent and yields SSE-formatted events.

    Each yielded string is a complete SSE frame:
    ``data: <json>\\n\\n``

    Args:
        request (`ChatRequest`):
            The incoming chat request containing the session id and message.

    Yields:
        `str`:
            An SSE frame carrying a JSON-serialised :class:`AgentEvent`.
    """
    from ...event import AgentEvent  # noqa: F401 – imported for type hint

    # TODO: retrieve the agent / session from the session registry
    # TODO: construct the user Msg from ``request.message``
    # TODO: call agent.reply(msg) which returns an AsyncGenerator[AgentEvent]
    # TODO: iterate over the agent event stream and yield each event below

    # TODO: replace the empty list below with the real agent event stream:
    #   async for event in agent.reply(user_msg):
    #       yield f"data: {event.model_dump_json()}\n\n"
    events: list[str] = []
    for event in events:
        yield f"data: {event}\n\n"


@chat_router.post(
    "/",
    summary="Chat with an agent (streaming)",
    response_description="Server-Sent Events stream of AgentEvent objects",
)
async def chat(request: ChatRequest) -> StreamingResponse:
    """Send a message to an agent and stream back the reply as SSE events.

    The response is a ``text/event-stream`` where each frame carries a
    JSON-serialised :class:`~agentscope.event.AgentEvent`.  Consumers should
    process the stream until the connection closes.

    Args:
        request (`ChatRequest`):
            JSON body with ``session_id`` and ``message``.

    Returns:
        `StreamingResponse`:
            An SSE stream of :data:`~agentscope.event.AgentEvent` frames.
    """
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
