# -*- coding: utf-8 -*-
"""Chat router — fire-and-forget trigger for chat runs.

The endpoint no longer returns an SSE stream. Instead, it kicks off a
chat run as a background task and returns immediately. Events produced
by the run are published to the message bus and delivered to the
frontend via the long-lived ``GET /sessions/{sid}/stream`` SSE
connection provided by the session router.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
)
from ._schema import ChatRequest, ChatTriggerResponse
from .._manager import ChatRunRegistry
from .._service import ChatService, SubagentHitlInbox
from ..message_bus import MessageBus
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

    The run executes as a background task tracked by
    :class:`ChatRunRegistry`. Events produced during the run are
    published to the message bus and delivered to any active
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
        chat_run_registry (`ChatRunRegistry`):
            Injected per-process chat-run registry.

    Returns:
        `ChatTriggerResponse`:
            Confirms the run was scheduled.

    Raises:
        `HTTPException`:
            409 if a chat run for this session is already in flight in
            this process (the registry enforces single-run-per-session).
    """
    try:
        # ------------------------------------------------------------
        # Subagent-confirm routing .
        #
        # A confirmation / external-result POSTed to a *leader* session may
        # actually belong to a team *member*: the leader is the single
        # front door clients talk to. Resolve the owning worker HERE,
        # before spawning, so the resume run is registered under the
        # **worker** session id — NOT the leader's.
        #
        # This is load-bearing for two failure modes:
        #   * The leader is usually *busy* (it is parked waiting on the
        #     member). Spawning under ``leader_sid`` would collide with
        #     its live run → 409 "already has an active chat run".
        #   * Occupying the leader's registry slot for the worker's whole
        #     resume also blocks the leader's own wake-up (e.g. the
        #     member's follow-up ``TeamSay``) from ever spawning a leader
        #     run — the hint would sit unseen until the user typed again.
        #
        # Spawning under ``worker_sid`` keeps the leader slot free and
        # lets the worker resume acquire its *own* session lock.
        # ------------------------------------------------------------
        run_session_id = request.session_id
        run_agent_id = request.agent_id
        run_input = request.input
        if isinstance(
            request.input,
            (UserConfirmResultEvent, ExternalExecutionResultEvent),
        ):
            inbox = SubagentHitlInbox(message_bus)
            target = await inbox.resolve(
                request.session_id,
                request.input.reply_id,
            )
            if target is not None:
                run_session_id = target["worker_session_id"]
                run_agent_id = target["worker_agent_id"]

                # ----------------------------------------------------
                # Drain the worker's parked-run tail before spawning.
                #
                # When a worker hits HITL it yields RequireUserConfirm,
                # which is projected onto the leader (card appears,
                # becomes clickable) BEFORE the worker run finishes:
                # the run still has to persist its reply + state and
                # release its session lock, and only then does the
                # registry's done-callback free the worker slot.
                #
                # If the user confirms inside that window, spawning the
                # resume under ``worker_sid`` would collide with the
                # still-registered (but about-to-finish) parked run and
                # raise "already has an active chat run" → a spurious
                # 409. The card was legitimately clickable, so this is
                # not a real conflict — just a tail we must let drain.
                #
                # Await that tail (bounded) so the slot AND the
                # distributed session lock are both free before we
                # spawn the resume. A timeout means the "run" is not a
                # quick parked tail but a genuinely live run, so we
                # fall through and let ``spawn`` surface the real 409.
                # ----------------------------------------------------
                existing = chat_run_registry.get(run_session_id)
                if existing is not None and not existing.done():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(existing),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        pass
                    except Exception:  # pylint: disable=broad-except
                        # The parked run's own errors are logged and
                        # swallowed inside ChatService.run; anything that
                        # still escapes here must not block the resume.
                        pass

        chat_run_registry.spawn(
            chat_service.run(
                user_id=user_id,
                session_id=run_session_id,
                agent_id=run_agent_id,
                input_msg=run_input,
            ),
            session_id=run_session_id,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    return ChatTriggerResponse(
        status="started",
        session_id=run_session_id,
    )
