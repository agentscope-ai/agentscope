# -*- coding: utf-8 -*-
"""Session router — create, list, update, delete, stream, and get messages."""
import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ..._utils._common import _generate_id
from ..access import ResourceKind
from ..deps import (
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_resource_access_service,
    get_session_service,
    get_storage,
    get_workspace_manager,
)
from ._schema import (
    CreateSessionRequest,
    CreateSessionResponse,
    InterruptSessionResponse,
    ListMessagesResponse,
    ListSessionsResponse,
    SessionStatusResponse,
    SessionView,
    TeamDetailResponse,
    TeamMemberView,
    UpdateSessionRequest,
)
from ..message_bus import MessageBus, MessageBusKeys
from .._service import (
    AgentView,
    ResourceAccessService,
    ChatService,
    SessionService,
    SessionProjection,
    SubagentHitlProjector,
)
from ..storage import (
    ChatModelConfig,
    SessionKnowledgeConfig,
    TTSModelConfig,
    SessionConfig,
    SessionRecord,
    StorageBase,
    TeamRecord,
)
from ...message import ToolCallState
from ..storage._utils import _ensure_team_members
from ...event import CustomEvent
from ..workspace_manager import WorkspaceManagerBase


async def _build_team_detail(
    storage: StorageBase,
    user_id: str,
    team: TeamRecord,
) -> TeamDetailResponse:
    """Resolve a team's leader agent + member agents into a
    :class:`TeamDetailResponse` for the session list endpoint.

    Args:
        storage (`StorageBase`):
            Application storage. Used to look up the leader session,
            each member agent, and each member's session.
        user_id (`str`):
            The owner user id.
        team (`TeamRecord`):
            The team to resolve. Caller has already loaded it.

    Returns:
        `TeamDetailResponse`:
            The team plus its resolved leader and member agents (each
            member paired with its session id when available).
    """
    leader_agent: AgentView | None = None
    leader_session = await storage.get_session(user_id, "", team.session_id)
    if leader_session is not None:
        leader_record = await storage.get_agent(
            user_id,
            leader_session.agent_id,
        )
        if leader_record is not None:
            leader_agent = AgentView.model_validate(
                {
                    **leader_record.model_dump(),
                    "editable": leader_record.user_id == user_id,
                },
            )

    members: list[TeamMemberView] = []
    for member in await _ensure_team_members(storage, user_id, team):
        agent = await storage.get_agent(member.owner_id, member.agent_id)
        if agent is None:
            continue
        # Use the member's team-scoped session id directly; an invited
        # agent has multiple sessions and only ``member.session_id``
        # belongs to this team. A member whose owner is not the current
        # user was invited from another user's pool — read-only here.
        members.append(
            TeamMemberView(
                agent=AgentView.model_validate(
                    {
                        **agent.model_dump(),
                        "editable": member.owner_id == user_id,
                    },
                ),
                session_id=member.session_id,
            ),
        )

    return TeamDetailResponse(
        team=team,
        leader_agent=leader_agent,
        members=members,
    )


session_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)


async def _ensure_credential_exists(
    access: ResourceAccessService,
    user_id: str,
    config: ChatModelConfig | TTSModelConfig | None,
) -> None:
    """Validate that the credential referenced by ``config`` is visible to
    the given user (own or shared). No-op when ``config`` is ``None``.

    Args:
        access (`ResourceAccessService`): Injected access service.
        user_id (`str`): The authenticated user ID.
        config (`ChatModelConfig | TTSModelConfig | None`): Model config to
            validate. Pass ``None`` to skip the check.

    Raises:
        `HTTPException`: 404 if the credential does not exist or is not
            visible to the user.
    """
    if config is None:
        return
    # ``get_resource`` raises 404 when the credential is neither owned
    # nor shared to the viewer — exactly the semantics we want.
    await access.get_resource(
        user_id,
        ResourceKind.CREDENTIAL,
        config.credential_id,
    )


async def _ensure_knowledge_bases_exist(
    access: ResourceAccessService,
    user_id: str,
    config: SessionKnowledgeConfig | None,
) -> None:
    """Validate every KB id in ``config`` is visible to the given user.

    No-op when ``config`` is ``None`` or its ``knowledge_base_ids``
    list is empty.

    Args:
        access (`ResourceAccessService`): Injected access service.
        user_id (`str`): The authenticated user ID.
        config (`SessionKnowledgeConfig | None`):
            Knowledge config to validate.  Pass ``None`` to skip.

    Raises:
        `HTTPException`: 404 if any KB id is not visible to the user.
    """
    if config is None or not config.knowledge_base_ids:
        return
    for kb_id in config.knowledge_base_ids:
        await access.get_resource(
            user_id,
            ResourceKind.KNOWLEDGE_BASE,
            kb_id,
        )


@session_router.get(
    "/",
    response_model=ListSessionsResponse,
    summary="List sessions for an agent",
)
async def list_sessions(
    agent_id: str = Query(description="Filter sessions by agent ID."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListSessionsResponse:
    """Return all sessions for an agent as enriched
    :class:`SessionView` entries.

    Each entry bundles three things the chat UI needs to render
    without follow-up requests: the session record (incl.
    ``state``), whether a chat run is currently active, and — when
    the session participates in a team — the resolved team detail
    (leader agent + member agents with their session ids).

    Args:
        agent_id (`str`):
            Agent whose sessions to list. May be an agent shared to
            the viewer through :class:`ResourceAccessPolicyBase`;
            the returned sessions are still the viewer's own.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.
        access (`ResourceAccessService`):
            Injected resource access service; used to validate that
            the target agent is visible to the viewer.
        message_bus (`MessageBus`):
            Injected message bus (used for ``session_is_running``).

    Returns:
        `ListSessionsResponse`:
            Enriched session views and their count.

    Raises:
        `HTTPException`: 404 if the agent is not visible to the caller.
    """
    # Verify visibility (own or shared) — raises 404 otherwise. Sessions
    # themselves are always looked up under ``user_id``, so a viewer
    # only ever sees their own runs of a shared agent.
    await access.resolve_agent(user_id, agent_id)

    sessions = await storage.list_sessions(user_id, agent_id)
    views: list[SessionView] = []
    for session in sessions:
        team_detail = None
        if session.team_id:
            team_record = await storage.get_team(user_id, session.team_id)
            if team_record is not None:
                team_detail = await _build_team_detail(
                    storage,
                    user_id,
                    team_record,
                )
        views.append(
            SessionView(
                session=session,
                is_running=await message_bus.is_locked(
                    MessageBusKeys.session_lock(session.id),
                ),
                team=team_detail,
            ),
        )
    return ListSessionsResponse(sessions=views, total=len(views))


@session_router.post(
    "/",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session(
    body: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> CreateSessionResponse:
    """Create (or resume) a session for a given agent and workspace.

    At most one session exists per ``(user_id, agent_id, workspace_id)``
    triple — a second call with the same triple updates the existing session
    rather than creating a duplicate.

    Args:
        body (`CreateSessionRequest`): Agent, workspace, and model config.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        workspace_manager (`WorkspaceManagerBase`): Injected workspace
            manager; consulted via
            :meth:`WorkspaceManagerBase.assign_workspace_id` to fill in
            ``workspace_id`` when the request body omits it.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `CreateSessionResponse`: The session identifier.

    Raises:
        `HTTPException`: 404 if the agent / credential / knowledge base
            is not visible to the caller.
    """
    # Agent must be visible to the caller (own or shared).
    await access.resolve_agent(user_id, body.agent_id)

    await _ensure_credential_exists(access, user_id, body.chat_model_config)
    await _ensure_credential_exists(
        access,
        user_id,
        body.fallback_chat_model_config,
    )
    await _ensure_credential_exists(access, user_id, body.tts_model_config)
    await _ensure_knowledge_bases_exist(
        access,
        user_id,
        body.knowledge_config,
    )

    # Resolve the workspace binding for this session. Explicit
    # ``body.workspace_id`` always wins (used by team invite / borrow
    # flows to force sharing); otherwise defer to the manager's
    # isolation policy — see ``WorkspaceManagerBase.assign_workspace_id``.
    resolved_workspace_id = body.workspace_id or (
        workspace_manager.assign_workspace_id(
            user_id=user_id,
            agent_id=body.agent_id,
            session_id=_generate_id(),
        )
    )

    session_record = await storage.upsert_session(
        user_id=user_id,
        agent_id=body.agent_id,
        config=SessionConfig(
            workspace_id=resolved_workspace_id,
            chat_model_config=body.chat_model_config,
            fallback_chat_model_config=body.fallback_chat_model_config,
            tts_model_config=body.tts_model_config,
            knowledge_config=body.knowledge_config,
            **({"name": body.name} if body.name is not None else {}),
        ),
    )
    return CreateSessionResponse(session_id=session_record.id)


@session_router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session",
)
async def delete_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
) -> None:
    """Permanently delete a session and all its associated state.

    Cancels any in-flight chat run for this session (and for every
    worker session if this one is a team leader) before dropping
    storage records and bus state. The cancel path is cross-process:
    whichever worker is actually running the session will receive the
    cancel broadcast and abort.

    Args:
        session_id (`str`): The session to delete.
        agent_id (`str`): The agent the session belongs to.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.

    Raises:
        `HTTPException`: 404 if the session does not exist or does not belong
            to the authenticated user.
    """
    deleted = await session_service.delete_session(
        user_id,
        agent_id,
        session_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )


@session_router.post(
    "/{session_id}/interrupt",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Interrupt a running or HITL-parked chat run for a session",
    responses={
        404: {"description": "Session not found."},
    },
)
async def interrupt_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> InterruptSessionResponse:
    """Request interruption of an in-progress reply for a session.

    Thin HTTP wrapper around :meth:`ChatService.interrupt`; see that
    method for the running vs not-running dispatch. Idempotent — an
    idle target session is a silent no-op at the agent layer.

    Args:
        session_id: The session whose reply should be interrupted.
        agent_id: The agent that owns the session.
        user_id: Injected authenticated user id.
        chat_service: Injected chat service.

    Returns:
        202 with :class:`InterruptSessionResponse` echoing the
        session id.

    Raises:
        HTTPException: 404 if the session does not exist.
    """
    try:
        await chat_service.interrupt(user_id, session_id, agent_id)
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return InterruptSessionResponse(session_id=session_id)


@session_router.patch(
    "/{session_id}",
    response_model=SessionRecord,
    summary="Update a session",
)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> SessionRecord:
    """Update the model configuration of an existing session.

    Args:
        session_id (`str`): The session to update.
        body (`UpdateSessionRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `SessionRecord`: The full session record after the update.

    Raises:
        `HTTPException`: 404 if the session does not exist, or if the
            referenced credential / KB is not visible to the caller.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    await _ensure_credential_exists(access, user_id, body.chat_model_config)
    await _ensure_credential_exists(
        access,
        user_id,
        body.fallback_chat_model_config,
    )
    await _ensure_credential_exists(access, user_id, body.tts_model_config)
    await _ensure_knowledge_bases_exist(
        access,
        user_id,
        body.knowledge_config,
    )

    updated_state = existing.state
    if body.permission_mode is not None:
        updated_ctx = existing.state.permission_context.model_copy(
            update={"mode": body.permission_mode},
        )

        updated_state = existing.state.model_copy(
            update={
                "permission_context": updated_ctx,
            },
        )

    # PATCH semantics: only fields explicitly present in the request body are
    # applied. ``exclude_unset=True`` lets clients distinguish "leave
    # unchanged" (omit) from "clear" (send ``null``) — required for clearing
    # ``fallback_chat_model_config``.
    config_updates = body.model_dump(
        exclude_unset=True,
        exclude={"permission_mode"},
    )

    return await storage.upsert_session(
        user_id=user_id,
        agent_id=agent_id,
        config=SessionConfig.model_validate(
            {**existing.config.model_dump(mode="json"), **config_updates},
        ),
        state=updated_state,
        session_id=session_id,
    )


# ----------------------------------------------------------------------
# Messages: fetch persisted messages for a session
# ----------------------------------------------------------------------


@session_router.get(
    "/{session_id}/messages",
    response_model=ListMessagesResponse,
    summary="List messages for a session",
)
async def list_messages(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(50, ge=1, le=200, description="Max messages."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListMessagesResponse:
    """Return persisted messages for a session.

    Args:
        session_id: The session to query.
        agent_id: Agent the session belongs to.
        offset: Pagination offset.
        limit: Maximum number of messages to return.
        user_id: Injected authenticated user ID.
        storage: Injected storage backend.
        message_bus: Injected message bus.

    Returns:
        Messages and running status.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    messages = await storage.list_messages(
        user_id,
        session_id,
        offset=offset,
        limit=limit,
    )
    return ListMessagesResponse(
        messages=messages,
        is_running=await message_bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ),
    )


# ----------------------------------------------------------------------
# Status probe: unified session status (cluster liveness + parking state)
# ----------------------------------------------------------------------


@session_router.get(
    "/{session_id}/status",
    response_model=SessionStatusResponse,
    summary="Probe the session's high-level status",
)
async def get_session_status(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
) -> SessionStatusResponse:
    """Return the unified :class:`SessionStatus` for a session.

    Ownership validation, cluster-liveness probing, and parked-state
    derivation are all delegated to
    :meth:`SessionService.get_session_status` — see that method for
    the precedence rules that collapse the two orthogonal signals
    (message-bus run lock + persisted context tail) into a single
    four-valued enum.

    Args:
        session_id (`str`):
            The session to probe.
        agent_id (`str`):
            The agent that owns the session (ownership validation).
        user_id (`str`):
            Injected authenticated user ID.
        session_service (`SessionService`):
            Injected session service. Owns both storage and message
            bus dependencies so the composed answer is derived in a
            single layer.

    Returns:
        `SessionStatusResponse`:
            The probed session id and its unified status.

    Raises:
        `HTTPException`: 404 if the session does not exist or does not
            belong to the authenticated user.
    """
    session_status = await session_service.get_session_status(
        user_id,
        agent_id,
        session_id,
    )
    if session_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return SessionStatusResponse(
        session_id=session_id,
        status=session_status,
    )


# ----------------------------------------------------------------------
# Stream: live SSE connection for session events
# ----------------------------------------------------------------------

_HEARTBEAT_INTERVAL_SECS = 30
# Interval between SSE heartbeat comment frames (``:\\n\\n``).


async def _worker_still_asking(
    storage: StorageBase,
    user_id: str,
    worker_agent_id: str,
    worker_session_id: str,
    reply_id: str,
) -> bool:
    """Return whether a worker session is still parked on the ASKING
    tool call identified by ``reply_id``.

    This is the reconcile-on-read check (design §3.5): the worker
    session's own ``state.context`` is the single source of truth for
    "does this confirmation still need answering". A leader-side
    pending projection whose worker has already resolved / cancelled
    the call is a ghost and must not be replayed.

    Mirrors the wakeup guard in
    :meth:`ChatService._run_impl` — a request is "still asking" when
    the tail ``AssistantMsg`` of the worker carries a tool call in
    ``ASKING`` or ``SUBMITTED`` state for the matching ``reply_id``.

    Args:
        storage (`StorageBase`):
            Application storage.
        user_id (`str`):
            The owner user id.
        worker_agent_id (`str`):
            The worker agent that owns the session.
        worker_session_id (`str`):
            The worker session to inspect.
        reply_id (`str`):
            The reply id the pending request belongs to.

    Returns:
        `bool`:
            ``True`` if the worker is still awaiting confirmation for
            ``reply_id``; ``False`` otherwise (resolved, cancelled, or
            the session/record is gone).
    """
    session = await storage.get_session(
        user_id,
        worker_agent_id,
        worker_session_id,
    )
    if session is None or not session.state.context:
        return False
    last_msg = session.state.context[-1]
    if last_msg.role != "assistant" or last_msg.id != reply_id:
        return False
    return any(
        tc.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        for tc in last_msg.get_content_blocks("tool_call")
    )


@session_router.get(
    "/{session_id}/stream",
    summary="Subscribe to a session's event stream (SSE)",
    response_description="Server-Sent Events stream of AgentEvent objects",
)
async def stream_session_events(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> StreamingResponse:
    """Subscribe to a session's live event stream.

    Returns a ``text/event-stream`` that first replays any buffered
    events from the current run's replay log (if a run is in progress
    or just finished), then streams live events as they are produced
    by :meth:`ChatService.run`. The connection stays open
    until the client disconnects — subsequent runs on the same session
    are delivered over the same connection.

    A heartbeat comment frame (``:\\n\\n``) is sent every 30 seconds to
    keep the connection alive through reverse proxies.

    Args:
        session_id (`str`):
            The session to subscribe to.
        agent_id (`str`):
            The agent that owns the session (used for ownership
            validation).
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend (ownership check only).
        message_bus (`MessageBus`):
            Injected message bus (replay + live subscription).

    Returns:
        `StreamingResponse`:
            SSE stream of AgentEvent frames + periodic heartbeats.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    async def _sse_generator() -> AsyncGenerator[str, None]:
        # 1. Replay buffered events from the current run (if any).
        for _entry_id, event in await message_bus.log_read(
            MessageBusKeys.session_events(session_id),
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 1b. Inject pending subagent HITL cards projected onto this
        #     session as a team leader (design §3.5). These live in a
        #     durable Redis hash — NOT in the replay log (trimmed per
        #     run) nor in the leader's own Msg history — so a fresh
        #     reconnect after the worker parked still surfaces them.
        #
        #     Reconcile-on-read: the worker session's own context is the
        #     SSOT. Inject only when the worker is still ASKING; drop and
        #     delete ghosts (worker resolved/cancelled without clearing).
        projection = SessionProjection(message_bus)
        for payload in await projection.list(
            session_id,
            SubagentHitlProjector.KIND,
        ):
            if not await _worker_still_asking(
                storage,
                user_id,
                payload["worker_agent_id"],
                payload["worker_session_id"],
                payload["reply_id"],
            ):
                await projection.delete(
                    session_id,
                    SubagentHitlProjector.KIND,
                    SubagentHitlProjector.entry_id(
                        payload["worker_session_id"],
                        payload["reply_id"],
                    ),
                )
                continue
            custom = CustomEvent(
                name=SubagentHitlProjector.EVT_REQUIRE,
                value=payload,
            )
            data = json.dumps(
                custom.model_dump(mode="json"),
                ensure_ascii=False,
            )

            yield f"data: {data}\n\n"

        # 2. Live subscribe via a background feeder task that pushes
        #    events into a queue. The main loop reads from the queue
        #    with a timeout so we can interleave heartbeat frames.
        #
        #    We avoid calling ``wait_for(__anext__())`` on the async
        #    generator directly because cancelling a suspended
        #    ``__anext__`` leaves the generator in a "running" state
        #    that prevents ``aclose()`` from working.
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _feeder() -> None:
            """Read from the bus subscription and forward to the queue.

            Pushes ``None`` as a sentinel when the subscription ends
            (which in practice only happens if the bus shuts down).
            """
            try:
                async for evt in message_bus.subscribe(
                    MessageBusKeys.session_events(session_id),
                ):
                    await queue.put(
                        {k: v for k, v in evt.items() if k != "_entry_id"},
                    )
            except asyncio.CancelledError:
                pass
            finally:
                await queue.put(None)

        feeder_task = asyncio.create_task(
            _feeder(),
            name=f"sse-feeder:{session_id}",
        )

        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=_HEARTBEAT_INTERVAL_SECS,
                    )
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ":\n\n"
        finally:
            feeder_task.cancel()
            try:
                await feeder_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
