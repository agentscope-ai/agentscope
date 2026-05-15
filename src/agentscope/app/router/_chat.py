# -*- coding: utf-8 -*-
"""Chat router providing a streaming SSE chat endpoint."""
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from .._deps import get_current_user_id, get_session_manager, get_storage
from .._manager import SessionManager
from .._schema._chat import ChatRequest
from ..storage import StorageBase

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


async def _stream_events(
    user_id: str,
    request: ChatRequest,
    storage: StorageBase,
    session_manager: SessionManager,
) -> AsyncGenerator[str, None]:
    """Drive the agent and yield SSE-formatted AgentEvent frames.

    Each yielded string is a complete SSE frame: ``data: <json>\\n\\n``.

    Args:
        user_id (`str`): Authenticated caller's user ID.
        request (`ChatRequest`): Parsed request body.
        storage (`StorageBase`): Application storage backend.
        session_manager (`SessionManager`): Application session manager.

    Yields:
        `str`: SSE frame carrying a JSON-serialised AgentEvent.
    """
    # ------------------------------------------------------------------
    # 1. Load and validate the session record
    # ------------------------------------------------------------------
    # TODO: load SessionRecord from storage
    #   session_record = await storage.get_session(user_id, request.session_id)
    #   if session_record is None:
    #       raise HTTPException(status_code=404, detail="Session not found")

    # ------------------------------------------------------------------
    # 2. Verify the agent belongs to this user
    # ------------------------------------------------------------------
    # TODO: load AgentRecord from storage and verify ownership
    #   agent_record = await storage.get_agent(user_id, request.agent_id)
    #   if agent_record is None:
    #       raise HTTPException(status_code=404, detail="Agent not found")

    # ------------------------------------------------------------------
    # 3. Assemble the Agent object from the stored record
    # ------------------------------------------------------------------
    # TODO: build the Agent from agent_record.data
    #   agent = Agent(
    #       name=agent_record.data.name,
    #       system_prompt=agent_record.data.system_prompt,
    #       context_config=agent_record.data.context_config,
    #       react_config=agent_record.data.react_config,
    #       ...
    #   )

    # ------------------------------------------------------------------
    # 4. Run the agent inside the session manager context
    #    (serialises concurrent requests; buffers events for replay)
    # ------------------------------------------------------------------
    async with session_manager.run(request.session_id) as run:
        # TODO: call agent.reply(request.input) and stream events
        #   async for event in agent.reply(request.input):
        #       await run.publish(event)
        #       yield f"data: {event.model_dump_json()}\n\n"
        pass

    # ------------------------------------------------------------------
    # 5. Persist the completed reply as a Msg in storage
    # ------------------------------------------------------------------
    # TODO: convert the buffered events in `run.buffer` to Msg objects
    #   and persist them via storage.upsert_session / storage.append_message


@chat_router.post(
    "/",
    summary="Chat with an agent (streaming)",
    response_description="Server-Sent Events stream of AgentEvent objects",
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    session_manager: SessionManager = Depends(get_session_manager),
) -> StreamingResponse:
    """Send a message to an agent and stream back the reply as SSE events.

    The response is a ``text/event-stream`` where each frame carries a
    JSON-serialised :class:`~agentscope.event.AgentEvent`.

    Args:
        request (`ChatRequest`): JSON body with ``agent_id``, ``session_id``,
            and ``input``.
        user_id (`str`): Injected by :func:`~agentscope.app._deps.get_current_user_id`.
        storage (`StorageBase`): Injected application storage backend.
        session_manager (`SessionManager`): Injected session manager.

    Returns:
        `StreamingResponse`: SSE stream of AgentEvent frames.
    """
    return StreamingResponse(
        _stream_events(user_id, request, storage, session_manager),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
