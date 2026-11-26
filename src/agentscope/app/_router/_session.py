# -*- coding: utf-8 -*-
"""Session router — create, list, update, delete, stream, and get messages."""
import asyncio
import json
from contextlib import AsyncExitStack
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

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
    SessionStatus,
    SessionProjection,
    SubagentHitlProjector,
    validate_tts_model_config,
)
from ..storage import (
    ASRModelConfig,
    ChatModelConfig,
    RealtimeModelConfig,
    SessionKnowledgeConfig,
    TTSModelConfig,
    SessionConfig,
    SessionNaming,
    ScheduleOrigin,
    SessionRecord,
    StorageBase,
    TeamRecord,
)
from ...state._state import ScheduleSessionLimits
from ._schema._session import ListedSession
from ..storage._utils import _ensure_team_members, _resolve_team_leader
from ...event import CustomEvent
from ..workspace_manager import WorkspaceManagerBase


async def _build_team_detail(
    storage: StorageBase,
    access: ResourceAccessService,
    user_id: str,
    team: TeamRecord,
) -> TeamDetailResponse:
    """Resolve a team's leader agent + member agents into a
    :class:`TeamDetailResponse` for the session list endpoint.

    Args:
        storage (`StorageBase`):
            Application storage. Used to look up the leader session,
            each member agent, and each member's session.
        access (`ResourceAccessService`):
            Resolves roster agents against the viewer's current grants.
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
    leader = await _resolve_team_leader(storage, user_id, team)
    if leader is not None:
        leader_agent = AgentView.model_validate(
            {
                **leader.agent.model_dump(),
                "editable": leader.agent.user_id == user_id,
            },
        )

    members: list[TeamMemberView] = []
    for member in await _ensure_team_members(storage, user_id, team):
        agent = await access.try_resolve_agent(user_id, member.agent_id)
        if agent is None or agent.user_id != member.owner_id:
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
    config: (
        ASRModelConfig
        | ChatModelConfig
        | RealtimeModelConfig
        | TTSModelConfig
        | None
    ),
) -> None:
    """Validate that the credential referenced by ``config`` is visible to
    the given user (own or shared). No-op when ``config`` is ``None``.

    Args:
        access (`ResourceAccessService`): Injected access service.
        user_id (`str`): The authenticated user ID.
        config (`ASRModelConfig | ChatModelConfig | RealtimeModelConfig | \
            TTSModelConfig | None`): Model config to validate. Pass ``None`` \
            to skip the check.

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
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ListSessionsResponse:
    """Return all sessions for an agent as enriched
    :class:`SessionView` entries.

    Each entry bundles what the chat UI needs to render without
    follow-up requests: the session record, its status, and — when
    the session participates in a team — the resolved team detail
    (leader agent + member agents with their session ids).

    The list exposes only effective permissions and tasks from runtime
    state. Fetch a session's messages
    from ``GET /sessions/{id}/messages`` instead.

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
        limit (`int | None`):
            Maximum number of sessions; omitted for the legacy full list.
        offset (`int`):
            Number of sessions to skip in newest-first order.

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

    sessions, total = await storage.list_sessions_page(
        user_id,
        agent_id,
        limit=limit,
        offset=offset,
    )
    views: list[SessionView] = []
    for item in sessions:
        session = item.session
        team_detail = None
        if session.team_id:
            team_record = await storage.get_team(user_id, session.team_id)
            if team_record is not None:
                team_detail = await _build_team_detail(
                    storage,
                    access,
                    user_id,
                    team_record,
                )
        is_running = await message_bus.is_locked(
            MessageBusKeys.session_execution_lock(session.id),
        )
        # The live lease overrides the persisted tail tool-call states.
        session_status = (
            SessionStatus.RUNNING
            if is_running
            else SessionService.derive_tool_call_status(item.tool_call_states)
        )
        views.append(
            SessionView(
                session=ListedSession.from_record(session),
                is_running=is_running,
                status=session_status,
                team=team_detail,
            ),
        )
    return ListSessionsResponse(sessions=views, total=total)


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
    await validate_tts_model_config(
        user_id,
        body.tts_model_config,
        access,
        storage,
    )
    await _ensure_credential_exists(access, user_id, body.asr_model_config)
    await _ensure_credential_exists(
        access,
        user_id,
        body.realtime_model_config,
    )
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
        await workspace_manager.assign_workspace_id(
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
            asr_model_config=body.asr_model_config,
            realtime_model_config=body.realtime_model_config,
            knowledge_config=body.knowledge_config,
            # A caller that named the session owns that name; anything
            # else starts on the creation timestamp and is the server's
            # to replace once the conversation says what it is about.
            naming=SessionNaming(auto=body.name is None),
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
    response_model=ListedSession,
    summary="Update a session",
)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListedSession:
    """Update the configuration of an existing session.

    Rejected while a chat run holds the session: the agent snapshots
    its configuration once at run start, so a mid-run change would be
    ignored for the current reply anyway — and writing it would race
    the run's own state persistence.

    Args:
        session_id (`str`): The session to update.
        body (`UpdateSessionRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.
        message_bus (`MessageBus`): Injected message bus; used to
            detect an in-flight run.

    Returns:
        `ListedSession`: The saved configuration and public session state.

    Raises:
        `HTTPException`: 404 if the session does not exist, or if the
            referenced credential / KB is not visible to the caller;
            409 if a chat run is currently active on the session.
    """
    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(
                message_bus.acquire_lock_bounded(
                    MessageBusKeys.session_lock(session_id),
                ),
            )
        except TimeoutError as exc:
            if (
                await storage.get_session(user_id, agent_id, session_id)
                is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Session '{session_id}' not found.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot modify session configuration while the session "
                "is running or another update is in progress.",
            ) from exc
        existing = await storage.get_session(user_id, agent_id, session_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session '{session_id}' not found.",
            )
        saved = await _apply_session_patch(existing, body, access, storage)
        return ListedSession.from_record(saved)
    raise RuntimeError("Session lock suppressed a session update error")


async def _apply_session_patch(
    existing: SessionRecord,
    body: UpdateSessionRequest,
    access: ResourceAccessService,
    storage: StorageBase,
) -> SessionRecord:
    """Merge a patch into the latest record while holding its session lock."""
    user_id = existing.user_id
    await _ensure_credential_exists(access, user_id, body.chat_model_config)
    await _ensure_credential_exists(
        access,
        user_id,
        body.fallback_chat_model_config,
    )
    await validate_tts_model_config(
        user_id,
        body.tts_model_config,
        access,
        storage,
    )
    await _ensure_credential_exists(access, user_id, body.asr_model_config)
    await _ensure_credential_exists(
        access,
        user_id,
        body.realtime_model_config,
    )
    await _ensure_knowledge_bases_exist(
        access,
        user_id,
        body.knowledge_config,
    )

    # ``None`` leaves the stored state untouched (see
    # ``StorageBase.upsert_session``). Permission settings live inside
    # ``state``; every other field is a pure config write. Rewriting
    # ``state`` for those would put
    # this handler's opening snapshot back over whatever the run has
    # persisted since.
    updated_state = None
    permission_fields = {
        "permission_mode": "mode",
        "read_only": "read_only",
        "allow_workspace_edits": "allow_workspace_edits",
        "interactive": "interactive",
    }
    permission_updates = {
        target: getattr(body, source)
        for source, target in permission_fields.items()
        if getattr(body, source) is not None
    }
    if permission_updates:
        updated_ctx = existing.state.permission_context.model_copy(
            update=permission_updates,
            deep=True,
        )

        updated_state = existing.state.model_copy(
            update={
                "permission_context": updated_ctx,
            },
        )

        if isinstance(existing.origin, ScheduleOrigin):
            limits = (
                existing.state.schedule_session_limits
                or ScheduleSessionLimits.from_context(
                    existing.state.permission_context,
                )
            )
            updated_state.schedule_session_limits = limits.model_copy(
                update={
                    key: value
                    for key, value in permission_updates.items()
                    if key in ScheduleSessionLimits.model_fields
                },
            )
            schedule = await storage.get_schedule(
                user_id,
                existing.origin.schedule_id,
            )
            if schedule is not None:
                schedule.apply_to_session(updated_state)
            updated_state.permission_context.interactive = False

    # PATCH semantics: only fields explicitly present in the request body are
    # applied. ``exclude_unset=True`` lets clients distinguish "leave
    # unchanged" (omit) from "clear" (send ``null``) — required for clearing
    # ``fallback_chat_model_config``.
    config_updates = body.model_dump(
        exclude_unset=True,
        exclude=set(permission_fields),
    )

    # An explicit rename settles the name for good — auto-naming must
    # not overwrite what the user just typed.
    if "name" in config_updates:
        config_updates["naming"] = {
            **existing.config.naming.model_dump(),
            "auto": False,
        }

    try:
        updated_config = SessionConfig.model_validate(
            {**existing.config.model_dump(mode="json"), **config_updates},
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    return await storage.upsert_session(
        user_id=user_id,
        agent_id=existing.agent_id,
        config=updated_config,
        state=updated_state,
        session_id=existing.id,
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
    before: str
    | None = Query(
        None,
        description=(
            "A message ID used as the pagination cursor. Omit to get "
            "the latest page. Provide a message ID from a previous "
            "response to load older messages."
        ),
    ),
    offset: int
    | None = Query(
        None,
        deprecated=True,
        description=(
            "**Deprecated.** Use ``before`` for cursor-based pagination. "
            "This parameter is always ignored."
        ),
    ),
    limit: int = Query(50, ge=1, le=200, description="Max messages."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListMessagesResponse:
    """Return persisted messages for a session with cursor-based
    pagination.

    Args:
        session_id: The session to query.
        agent_id: Agent the session belongs to.
        before: A message ID used as the pagination cursor. Omit for
            the latest page; provide a message ID from a previous
            response to load older messages.
        offset: **Deprecated.** Numeric offset, always ignored. Still
            accepted for backward compatibility.
        limit: Maximum number of messages to return.
        user_id: Injected authenticated user ID.
        storage: Injected storage backend.
        message_bus: Injected message bus.

    Returns:
        Messages, their replay cursor, running status, and whether more
        pages exist.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    # Forward deprecated offset via kwargs so storage can warn.
    extra: dict = {}
    if offset is not None:
        extra["offset"] = offset

    event_cursor = None
    if before is not None or offset is not None:
        messages, has_more = await storage.list_messages(
            user_id,
            session_id,
            limit=limit,
            before=before,
            **extra,
        )
    else:
        checkpoint_key = MessageBusKeys.session_event_checkpoint(session_id)
        async with message_bus.acquire_lock(
            MessageBusKeys.session_event_checkpoint_lock(session_id),
            ttl_secs=(MessageBusKeys.SESSION_EVENT_CHECKPOINT_LOCK_TTL_SECS),
        ):
            event_cursor = await message_bus.registry_get(
                checkpoint_key,
                MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
            )
            messages, has_more = await storage.list_messages(
                user_id,
                session_id,
                limit=limit,
                before=before,
                **extra,
            )
    return ListMessagesResponse(
        messages=messages,
        event_cursor=event_cursor,
        is_running=await message_bus.is_locked(
            MessageBusKeys.session_execution_lock(session_id),
        ),
        has_more=has_more,
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
_SUBSCRIBE_TIMEOUT_SECS = 5.0
# Interval between SSE heartbeat comment frames (``:\\n\\n``).


@session_router.get(
    "/{session_id}/stream",
    summary="Subscribe to a session's event stream (SSE)",
    response_description="Server-Sent Events stream of AgentEvent objects",
)
async def stream_session_events(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    after: str
    | None = Query(
        None,
        description=("Replay only events after this checkpoint cursor."),
    ),
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
        after (`str | None`):
            Persisted replay cursor returned by the messages endpoint.
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
        events_key = MessageBusKeys.session_events(session_id)
        ready = asyncio.Event()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def _feeder() -> None:
            """Read from the bus subscription and forward to the queue.

            Pushes ``None`` as a sentinel when the subscription ends
            (which in practice only happens if the bus shuts down).
            """
            try:
                async for evt in message_bus.subscribe(
                    events_key,
                    on_ready=ready.set,
                ):
                    await queue.put(evt)
            except asyncio.CancelledError:
                pass
            finally:
                await queue.put(None)

        feeder_task = asyncio.create_task(
            _feeder(),
            name=f"sse-feeder:{session_id}",
        )

        try:
            # Subscribe first, then replay. Events arriving across that
            # seam are present in both paths and deduplicated by entry id.
            await asyncio.wait_for(
                ready.wait(),
                timeout=_SUBSCRIBE_TIMEOUT_SECS,
            )
            seen_entry_ids: set[str] = set()
            for entry_id, event in await message_bus.log_read(
                events_key,
                since=after,
                max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
            ):
                seen_entry_ids.add(str(entry_id))
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # Inject pending subagent HITL cards projected onto this
            # session as a team leader. These live in a durable registry,
            # outside the replay log.
            projection = SessionProjection(message_bus)
            pending = []
            cards = await SubagentHitlProjector(storage).snapshot(
                user_id,
                existing,
                await projection.list(session_id, SubagentHitlProjector.KIND),
                message_bus,
            )
            for card in cards:
                pending.append(
                    {
                        "worker_session_id": card["worker_session_id"],
                        "reply_id": card["reply_id"],
                        "request_id": card["event"]["id"],
                    },
                )
                custom = CustomEvent(
                    name=SubagentHitlProjector.EVT_REQUIRE,
                    value=card,
                )
                data = json.dumps(
                    custom.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield f"data: {data}\n\n"

            # Cards above carry the full payload. Send their membership once
            # so reconnecting clients can also remove cards closed offline.
            snapshot = CustomEvent(
                name="subagent_hitl_snapshot",
                value={"pending": pending},
            )
            yield f"data: {snapshot.model_dump_json()}\n\n"

            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=_HEARTBEAT_INTERVAL_SECS,
                    )
                    if item is None:
                        break
                    entry_id = item.get("_entry_id")
                    if entry_id is not None:
                        entry_id = str(entry_id)
                        if entry_id in seen_entry_ids:
                            continue
                    payload = {
                        key: value
                        for key, value in item.items()
                        if key != "_entry_id"
                    }
                    data = json.dumps(payload, ensure_ascii=False)
                    yield f"data: {data}\n\n"
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
