# -*- coding: utf-8 -*-
"""Chat router — fire-and-forget trigger for chat runs.

The endpoint no longer returns an SSE stream. Instead, it kicks off a
chat run as a background task and returns immediately. Events produced
by the run are published to the message bus and delivered to the
frontend via the long-lived ``GET /sessions/{sid}/stream`` SSE
connection provided by the session router.

Two trigger paths, deliberately asymmetric:

- **New user message(s)** are spawned directly into the
  :class:`ChatRunRegistry`. The registry's single-run-per-session rule
  surfaces as a 409, which is exactly the desired double-submit guard.
- **HITL results** (``UserConfirmResultEvent`` /
  ``ExternalExecutionResultEvent``) are *enqueued* onto the shared
  run-trigger queue and drained by the single
  :class:`WakeupDispatcher`. Routing the resume through the queue keeps
  the dispatcher the sole spawn site, so a resume can never collide with
  the worker's still-finishing parked run (the old 409 race) — the
  dispatcher serialises them.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_session_service,
    get_storage,
)
from ._schema import ChatRequest, ChatTriggerResponse
from .._manager import ChatRunRegistry
from .._service import (
    ChatService,
    SessionService,
    SessionProjection,
    SubagentHitlProjector,
)
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import SessionRecord, StorageBase
from .._bus_ops import enqueue_run_trigger
from ...event import UserConfirmResultEvent, ExternalExecutionResultEvent
from ...message import ToolCallState
from .._command import CommandContext, dispatch_command, parse_command

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


def _is_current_hitl_result(
    session: SessionRecord,
    event: UserConfirmResultEvent | ExternalExecutionResultEvent,
) -> bool:
    """Whether an HITL result still targets the parked session state."""
    if session.state.reply_id != event.reply_id or not session.state.context:
        return False

    last_msg = session.state.context[-1]
    if last_msg.role != "assistant":
        return False
    tool_calls = last_msg.get_content_blocks("tool_call")
    result_ids = {
        block.id for block in last_msg.get_content_blocks("tool_result")
    }

    if isinstance(event, UserConfirmResultEvent):
        awaiting_ids = {
            block.id
            for block in tool_calls
            if block.state == ToolCallState.ASKING
        }
        incoming_ids = {
            result.tool_call.id for result in event.confirm_results
        }
    else:
        awaiting_ids = {
            block.id
            for block in tool_calls
            if block.state == ToolCallState.SUBMITTED
            and block.id not in result_ids
        }
        incoming_ids = {result.id for result in event.execution_results}

    return bool(awaiting_ids) and incoming_ids.issubset(awaiting_ids)


@chat_router.post(
    "/",
    response_model=ChatTriggerResponse,
    summary="Trigger a chat run (fire-and-forget)",
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    message_bus: MessageBus = Depends(get_message_bus),
    storage: StorageBase = Depends(get_storage),
    session_service: SessionService = Depends(get_session_service),
) -> ChatTriggerResponse:
    """Trigger a chat run for the specified session.

    Events produced during the run are published to the message bus and
    delivered to any active ``GET /sessions/{session_id}/stream`` SSE
    subscriber. The caller does **not** receive events from this
    endpoint's response body.

    Accepts the same ``input`` payloads as before:

    - ``Msg`` / ``list[Msg]``: new user message(s) — spawned directly.
    - ``UserConfirmResultEvent`` / ``ExternalExecutionResultEvent``:
      resume a paused tool call (human-in-the-loop) — routed to the
      owning session and enqueued for the dispatcher.
    - ``None``: continue from current state — spawned directly.

    Args:
        request (`ChatRequest`):
            JSON body with ``agent_id``, ``session_id``, and ``input``.
        user_id (`str`):
            Injected user id.
        chat_service (`ChatService`):
            Injected application-wide chat service.
        chat_run_registry (`ChatRunRegistry`):
            Injected per-process chat-run registry.
        message_bus (`MessageBus`):
            Injected message bus, used to resolve subagent-confirm
            routing and to enqueue resume triggers.

    Returns:
        `ChatTriggerResponse`:
            Confirms the run was scheduled (for a resume, that it was
            enqueued).

    Raises:
        `HTTPException`:
            409 if a chat run for this session is already in flight in
            this process (the registry enforces single-run-per-session).
            Only direct-spawn paths (new messages / ``None``) can raise
            this; the enqueued resume path never does.
    """
    command = parse_command(request.input)
    if command is not None:
        if command.args and not command.spec.accepts_args:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"/{command.spec.name} does not accept arguments.",
            )
        try:
            result = await dispatch_command(
                command,
                CommandContext(
                    user_id=user_id,
                    agent_id=request.agent_id,
                    session_id=request.session_id,
                    source="http",
                    command_message_id=command.message.id,
                ),
                session_service,
            )
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        return ChatTriggerResponse(
            status="command_completed",
            session_id=result.root_session_id,
            command=result.name,
        )

    # ------------------------------------------------------------------
    # HITL resume — route to the owning session, then enqueue.
    #
    # A confirmation / external-result POSTed to a *leader* session may
    # actually belong to a team *member*: the leader is the single front
    # door clients talk to. Resolve the owning worker HERE, then enqueue
    # a ``resume`` trigger for that session. The single WakeupDispatcher
    # drains it — spawning under the *worker* session id, serialised
    # behind any still-finishing parked run, so there is no registry
    # collision (no 409) and the leader's run slot is never occupied by
    # the worker's resume.
    # ------------------------------------------------------------------
    if isinstance(
        request.input,
        (UserConfirmResultEvent, ExternalExecutionResultEvent),
    ):
        run_session_id = request.session_id
        run_agent_id = request.agent_id
        target = await SubagentHitlProjector.resolve(
            SessionProjection(message_bus),
            request.session_id,
            request.input.reply_id,
        )
        if target is not None:
            run_session_id = target["worker_session_id"]
            run_agent_id = target["worker_agent_id"]

        target_session = await storage.get_session(
            user_id,
            run_agent_id,
            run_session_id,
        )
        if target_session is None or target_session.agent_id != run_agent_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {run_session_id!r} not found.",
            )
        if not _is_current_hitl_result(target_session, request.input):
            return ChatTriggerResponse(
                status="started",
                session_id=run_session_id,
            )
        await enqueue_run_trigger(
            message_bus,
            user_id=user_id,
            session_id=run_session_id,
            agent_id=run_agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=request.input,
            conversation_revision=target_session.conversation_revision,
        )
        return ChatTriggerResponse(status="started", session_id=run_session_id)

    # ------------------------------------------------------------------
    # New user message(s) / None — spawn directly. The registry's
    # single-run-per-session rule is the desired double-submit guard.
    # ------------------------------------------------------------------
    session = await storage.get_session(
        user_id,
        request.agent_id,
        request.session_id,
    )
    if session is None or session.agent_id != request.agent_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {request.session_id!r} not found.",
        )
    if await message_bus.registry_getall(
        MessageBusKeys.session_reset_barrier(request.session_id),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is resetting.",
        )

    try:
        chat_run_registry.spawn(
            chat_service.run(
                user_id=user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                input_msg=request.input,
                accepted_revision=session.conversation_revision,
            ),
            session_id=request.session_id,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return ChatTriggerResponse(
        status="started",
        session_id=request.session_id,
    )
