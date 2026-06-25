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
)
from ._schema import ChatRequest, ChatTriggerResponse
from .._manager import ChatRunRegistry
from .._service import (
    ChatService,
    SessionProjection,
    SubagentHitlProjector,
)
from ..message_bus import MessageBus, MessageBusKeys
from .._bus_ops import enqueue_run_trigger
from ...event import UserConfirmResultEvent, ExternalExecutionResultEvent

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
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    message_bus: MessageBus = Depends(get_message_bus),
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

        await enqueue_run_trigger(
            message_bus,
            user_id=user_id,
            session_id=run_session_id,
            agent_id=run_agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=request.input,
        )
        return ChatTriggerResponse(status="started", session_id=run_session_id)

    # ------------------------------------------------------------------
    # New user message(s) / None — spawn directly. The registry's
    # single-run-per-session rule is the desired double-submit guard.
    # ------------------------------------------------------------------
    try:
        chat_run_registry.spawn(
            chat_service.run(
                user_id=user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                input_msg=request.input,
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
