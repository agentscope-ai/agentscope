# -*- coding: utf-8 -*-
"""Chat router — fire-and-forget trigger for chat runs.

The endpoint no longer returns an SSE stream. Instead, it kicks off a
chat run as a background task and returns immediately. Events produced
by the run are published to the message bus and delivered to the
frontend via the long-lived ``GET /sessions/{sid}/stream`` SSE
connection provided by the session router.

Three trigger paths, deliberately asymmetric:

- **New user message(s)** are appended to a per-session FIFO and drained
  by :class:`WakeupDispatcher`, one complete reply at a time.
- **HITL results** (``UserConfirmResultEvent`` /
  ``ExternalExecutionResultEvent``) are *enqueued* onto the shared
  run-trigger queue and drained by the single
  :class:`WakeupDispatcher`. Routing the resume through the queue keeps
  the dispatcher the sole spawn site, so a resume can never collide with
  the worker's still-finishing parked run (the old 409 race) — the
  dispatcher serialises them.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from ..deps import (
    get_chat_run_registry,
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from ._schema import (
    ChatQueueResponse,
    ChatRequest,
    ChatTriggerResponse,
    ReorderChatQueueRequest,
    UpdateChatQueueItemRequest,
)
from .._manager import ChatRunRegistry
from .._service import (
    ChatService,
    SessionProjection,
    SubagentHitlProjector,
)
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from .._bus_ops import (
    ChatQueueBusyError,
    ChatQueueFullError,
    ChatQueuePayloadTooLargeError,
    delete_chat_input,
    enqueue_chat_input,
    enqueue_run_trigger,
    list_chat_inputs,
    reorder_chat_inputs,
    update_chat_input,
)
from ...event import UserConfirmResultEvent, ExternalExecutionResultEvent
from ...message import Msg

chat_router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    responses={404: {"description": "Not found"}},
)


async def _ensure_session(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> None:
    """Ensure the caller owns the target session before queue access.

    Args:
        storage (`StorageBase`):
            Persistent session storage.
        user_id (`str`):
            Authenticated user that must own the session.
        agent_id (`str`):
            Agent that must own the session.
        session_id (`str`):
            Session to validate.

    Raises:
        `HTTPException`:
            HTTP 404 when the session does not exist for this owner.
    """
    session = await storage.get_session(user_id, agent_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )


@chat_router.get(
    "/queue",
    response_model=ChatQueueResponse,
    summary="List editable pending user turns",
)
async def get_chat_queue(
    agent_id: Annotated[
        str,
        Query(description="Agent that owns the target session."),
    ],
    session_id: Annotated[
        str,
        Query(description="Session whose pending FIFO should be listed."),
    ],
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ChatQueueResponse:
    """Return ordinary user turns that have not begun execution.

    Args:
        agent_id (`str`):
            Agent that owns the target session.
        session_id (`str`):
            Session whose editable pending FIFO should be returned.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected persistent session storage.
        message_bus (`MessageBus`):
            Injected message bus containing the pending FIFO.

    Returns:
        `ChatQueueResponse`:
            Complete editable pending queue in FIFO order.

    Raises:
        `HTTPException`:
            HTTP 404 if the session is not owned by the caller, or HTTP
            503 if the queue cannot be read promptly.
    """
    await _ensure_session(storage, user_id, agent_id, session_id)
    try:
        items = await list_chat_inputs(message_bus, session_id)
    except ChatQueueBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ChatQueueResponse(items=items)


@chat_router.patch(
    "/queue/order",
    response_model=ChatQueueResponse,
    summary="Reorder editable pending user turns",
)
async def reorder_chat_queue(
    request: ReorderChatQueueRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ChatQueueResponse:
    """Apply an exact FIFO permutation, rejecting stale snapshots.

    Args:
        request (`ReorderChatQueueRequest`):
            Target session and exact desired pending-item permutation.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected persistent session storage.
        message_bus (`MessageBus`):
            Injected message bus containing the pending FIFO.

    Returns:
        `ChatQueueResponse`:
            Complete editable queue after reordering.

    Raises:
        `HTTPException`:
            HTTP 404 if the session is not owned by the caller, HTTP 409
            for a stale or invalid permutation, or HTTP 503 when the queue
            mutation lock cannot be acquired promptly.
    """
    await _ensure_session(
        storage,
        user_id,
        request.agent_id,
        request.session_id,
    )
    try:
        items = await reorder_chat_inputs(
            message_bus,
            user_id,
            request.session_id,
            request.agent_id,
            request.item_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ChatQueueBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ChatQueueResponse(items=items)


@chat_router.patch(
    "/queue/{item_id}",
    response_model=ChatQueueResponse,
    summary="Edit one pending user turn",
)
async def update_chat_queue_item(
    item_id: Annotated[
        str,
        Path(description="Stable id of the pending item to update."),
    ],
    request: UpdateChatQueueItemRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ChatQueueResponse:
    """Replace a pending item; started items produce HTTP 409.

    Args:
        item_id (`str`):
            Stable id of the pending item to update.
        request (`UpdateChatQueueItemRequest`):
            Target session and replacement message payload.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected persistent session storage.
        message_bus (`MessageBus`):
            Injected message bus containing the pending FIFO.

    Returns:
        `ChatQueueResponse`:
            Complete editable queue after the update.

    Raises:
        `HTTPException`:
            HTTP 404 if the session is not owned by the caller, HTTP 409
            if the item is no longer pending, HTTP 413 if the replacement
            is too large, or HTTP 503 if the queue is busy.
    """
    await _ensure_session(
        storage,
        user_id,
        request.agent_id,
        request.session_id,
    )
    try:
        items = await update_chat_input(
            message_bus,
            user_id,
            request.session_id,
            request.agent_id,
            item_id,
            request.input,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ChatQueuePayloadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except ChatQueueBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ChatQueueResponse(items=items)


@chat_router.delete(
    "/queue/{item_id}",
    response_model=ChatQueueResponse,
    summary="Delete one pending user turn",
)
async def delete_chat_queue_item(
    item_id: Annotated[
        str,
        Path(description="Stable id of the pending item to delete."),
    ],
    agent_id: Annotated[
        str,
        Query(description="Agent that owns the target session."),
    ],
    session_id: Annotated[
        str,
        Query(description="Session whose pending item should be deleted."),
    ],
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ChatQueueResponse:
    """Delete a pending item; started items produce HTTP 409.

    Args:
        item_id (`str`):
            Stable id of the pending item to delete.
        agent_id (`str`):
            Agent that owns the target session.
        session_id (`str`):
            Session whose pending item should be deleted.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected persistent session storage.
        message_bus (`MessageBus`):
            Injected message bus containing the pending FIFO.

    Returns:
        `ChatQueueResponse`:
            Complete editable queue after deletion.

    Raises:
        `HTTPException`:
            HTTP 404 if the session is not owned by the caller, HTTP 409
            if the item is no longer pending, or HTTP 503 if the queue is
            busy.
    """
    await _ensure_session(storage, user_id, agent_id, session_id)
    try:
        items = await delete_chat_input(
            message_bus,
            user_id,
            session_id,
            agent_id,
            item_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ChatQueueBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return ChatQueueResponse(items=items)


@chat_router.post(
    "/",
    response_model=ChatTriggerResponse,
    summary="Trigger a chat run (fire-and-forget)",
)
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
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

    - ``Msg`` / ``list[Msg]``: new user message(s) — queued FIFO.
    - ``UserConfirmResultEvent`` / ``ExternalExecutionResultEvent``:
      resume a paused tool call (human-in-the-loop) — routed to the
      owning session and enqueued for the dispatcher.
    - ``None``: continue from current state — spawned directly.

    Args:
        request (`ChatRequest`):
            JSON body with ``agent_id``, ``session_id``, and ``input``.
        user_id (`str`):
            Injected user id.
        storage (`StorageBase`):
            Injected persistent storage used to verify session ownership.
        chat_service (`ChatService`):
            Injected application-wide chat service.
        chat_run_registry (`ChatRunRegistry`):
            Injected per-process chat-run registry.
        message_bus (`MessageBus`):
            Injected message bus, used to resolve subagent-confirm
            routing and to enqueue ordinary or resume triggers.

    Returns:
        `ChatTriggerResponse`:
            Reports ``queued`` for an accepted ordinary turn and
            ``started`` for continuation or control triggers.

    Raises:
        `HTTPException`:
            HTTP 404 if an ordinary-message session is not owned by the
            caller; HTTP 409 for a conflicting direct ``None`` continuation;
            HTTP 413 for an oversized turn; HTTP 429 when a queue quota is
            full; or HTTP 503 when queue mutation is temporarily busy.
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

    # Ordinary user turns always enter the per-session FIFO. Using one
    # path for both idle and busy sessions removes the check-then-spawn
    # race and gives rapid submissions a stable order.
    if isinstance(request.input, (Msg, list)):
        await _ensure_session(
            storage,
            user_id,
            request.agent_id,
            request.session_id,
        )
        try:
            item = await enqueue_chat_input(
                message_bus,
                user_id=user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                inputs=request.input,
            )
        except ChatQueueFullError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
            ) from exc
        except ChatQueuePayloadTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except ChatQueueBusyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return ChatTriggerResponse(
            status="queued",
            session_id=request.session_id,
            queue_item_id=item["id"],
        )

    # ------------------------------------------------------------------
    # ``None`` continuation — retain the direct legacy path.
    # ------------------------------------------------------------------
    try:
        task = chat_run_registry.spawn(
            chat_service.run(
                user_id=user_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                input_msg=request.input,
            ),
            session_id=request.session_id,
        )
        task.add_done_callback(
            lambda _completed: chat_service.schedule_queue_nudge(
                user_id,
                request.session_id,
                request.agent_id,
            ),
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
