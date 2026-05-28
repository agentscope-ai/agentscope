# -*- coding: utf-8 -*-
"""Chat router providing a streaming SSE chat endpoint."""
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .._deps import (
    get_current_user_id,
    get_extra_agent_middlewares,
    get_extra_agent_tools,
    get_session_manager,
    get_storage,
    get_workspace_manager,
    get_background_task_manager,
)
from .._manager import (
    SessionManager,
    WorkspaceManagerBase,
    BackgroundTaskManager,
)
from .._schema import ChatRequest
from .._service import ChatService
from .._types import AgentMiddlewareFactory, AgentToolFactory
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
    workspace_manager: WorkspaceManagerBase,
    background_task_manager: BackgroundTaskManager,
    extra_agent_middlewares: AgentMiddlewareFactory | None,
    extra_agent_tools: AgentToolFactory | None,
) -> AsyncGenerator[str, None]:
    """Encode :class:`~agentscope.event.AgentEvent` objects as SSE frames.

    All execution + persistence lives in :class:`ChatService`; this is just
    the HTTP-side encoder.

    Each yielded string is a complete SSE frame: ``data: <json>\\n\\n``.
    """
    service = ChatService(
        storage=storage,
        session_manager=session_manager,
        background_task_manager=background_task_manager,
        workspace_manager=workspace_manager,
        extra_agent_middlewares=extra_agent_middlewares,
        extra_agent_tools=extra_agent_tools,
    )
    async for event in service.stream_chat(
        user_id=user_id,
        session_id=request.session_id,
        agent_id=request.agent_id,
        input_msg=request.input,
    ):
        yield f"data: {event.model_dump_json()}\n\n"


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
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    background_task_manager: BackgroundTaskManager = Depends(
        get_background_task_manager,
    ),
    extra_agent_middlewares: AgentMiddlewareFactory
    | None = Depends(
        get_extra_agent_middlewares,
    ),
    extra_agent_tools: AgentToolFactory
    | None = Depends(
        get_extra_agent_tools,
    ),
) -> StreamingResponse:
    """Send a message to an agent and stream back the reply as SSE events.

    The response is a ``text/event-stream`` where each frame carries a
    JSON-serialised :class:`~agentscope.event.AgentEvent`.

    Args:
        request (`ChatRequest`):
            JSON body with ``agent_id``, ``session_id``, and ``input``.
        user_id (`str`):
            Injected user id.
        storage (`StorageBase`):
            Injected application storage backend.
        session_manager (`SessionManager`):
            Injected session manager.
        workspace_manager (`WorkspaceManagerBase`):
            Injected workspace manager.
        background_task_manager (`BackgroundTaskManager`):
            Injected background task manager.

    Returns:
        `StreamingResponse`:
            SSE stream of AgentEvent frames.
    """
    return StreamingResponse(
        _stream_events(
            user_id,
            request,
            storage,
            session_manager,
            workspace_manager,
            background_task_manager,
            extra_agent_middlewares,
            extra_agent_tools,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
