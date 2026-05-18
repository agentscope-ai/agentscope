# -*- coding: utf-8 -*-
"""Chat router providing a streaming SSE chat endpoint."""
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from .._deps import (
    get_current_user_id,
    get_session_manager,
    get_storage,
    get_workspace_manager,
    get_background_task_manager,
)
from .._manager import SessionManager, WorkspaceManagerBase, BackgroundTaskManager
from .._schema import ChatRequest
from .._service import get_agent
from ..storage import StorageBase
from ..._logging import logger
from ...event import ReplyStartEvent
from ...message import Msg, AssistantMsg

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
) -> AsyncGenerator[str, None]:
    """Drive the agent and yield SSE-formatted AgentEvent frames.

    Each yielded string is a complete SSE frame: ``data: <json>\\n\\n``.
    Persists input messages and builds the reply Msg from events.
    """

    # ------------------------------------------------------------------
    # 1. Get the agent instance
    # ------------------------------------------------------------------
    agent = await get_agent(
        storage=storage,
        workspace_manager=workspace_manager,
        background_task_manager=background_task_manager,
        user_id=user_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
    )

    # ------------------------------------------------------------------
    # 2. Run the agent inside the session manager context
    # ------------------------------------------------------------------
    async with session_manager.run(request.session_id) as run:
        reply_msg: Msg | None = None

        if isinstance(request.input, (Msg, list)):
            # Case A: New user message(s)
            input_msgs = (
                [request.input]
                if isinstance(request.input, Msg)
                else request.input
            )
            for msg in input_msgs:
                await storage.upsert_message(
                    user_id,
                    request.session_id,
                    msg,
                )

            async for event in agent.reply_stream(msgs=request.input):
                await run.publish(event)
                yield f"data: {event.model_dump_json()}\n\n"
                if isinstance(event, ReplyStartEvent):
                    reply_msg = AssistantMsg(
                        id=event.reply_id,
                        name=event.name,
                        content=[],
                    )
                elif reply_msg is not None:
                    reply_msg.append_event(event)

        else:
            # Case B: Continuation (UserConfirmResult / ExternalExecResult)
            reply_msg = await storage.get_message(
                user_id,
                request.session_id,
                agent.state.reply_id,
            )

            if reply_msg is None:
                logger.warning(
                    "Reply message %r not found in storage for session %r; "
                    "tool-call state changes from the incoming event will not "
                    "be persisted.",
                    agent.state.reply_id,
                    request.session_id,
                )
            elif request.input:
                # Apply the incoming event so that the persisted message
                # reflects the updated tool-call states before streaming
                # resumes (e.g. ASKING→ALLOWED/FINISHED for
                # UserConfirmResultEvent, or appended ToolResultBlocks for
                # ExternalExecutionResultEvent).
                reply_msg.append_event(request.input)

            async for event in agent.reply_stream(event=request.input):
                await run.publish(event)
                yield f"data: {event.model_dump_json()}\n\n"
                if reply_msg is not None:
                    reply_msg.append_event(event)

        # Persist the reply Msg (upsert: overwrite if same id, append if new)
        if reply_msg is not None:
            await storage.upsert_message(
                user_id,
                request.session_id,
                reply_msg,
            )

    # ------------------------------------------------------------------
    # 3. Persist agent state
    # ------------------------------------------------------------------
    await storage.update_session_state(
        user_id,
        agent_id=request.agent_id,
        session_id=request.session_id,
        state=agent.state,
    )


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
    background_task_manager: BackgroundTaskManager = Depends(get_background_task_manager)
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
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
