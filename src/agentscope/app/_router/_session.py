# -*- coding: utf-8 -*-
"""Session router — create, list, update, delete, stream, and get messages."""
import asyncio
import inspect
import json
from collections import OrderedDict
from typing import AsyncGenerator, Awaitable, Callable
from uuid import uuid4

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
from ..message_bus._base import (
    RegisterSubscriptionBarrier,
    session_event_payload,
)
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
    PipelineVoiceConfig,
    ChatModelConfig,
    RealtimeModelConfig,
    SessionKnowledgeConfig,
    TTSModelConfig,
    SessionConfig,
    SessionNaming,
    SessionRecord,
    StorageBase,
    TeamRecord,
)
from ...message import ToolCallState
from ...state import ToolContext
from ..storage._utils import _ensure_team_members, _resolve_team_leader
from ...event import CustomEvent
from ..workspace_manager import WorkspaceManagerBase


async def _validate_pipeline_voice(
    config: PipelineVoiceConfig | None,
    user_id: str,
    access: ResourceAccessService,
    storage: StorageBase,
) -> None:
    """Validate independent voice credentials without enabling chat TTS."""
    if config is not None:
        await _ensure_credential_exists(
            access,
            user_id,
            config.chat_model_config,
        )
        await _ensure_credential_exists(
            access,
            user_id,
            config.asr_model_config,
        )
        await validate_tts_model_config(
            user_id,
            config.tts_model_config,
            access,
            storage,
        )


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


async def _validate_session_config(
    body: CreateSessionRequest | UpdateSessionRequest,
    user_id: str,
    access: ResourceAccessService,
    storage: StorageBase,
) -> None:
    """Validate the model and knowledge base references in a request."""
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
    await _validate_pipeline_voice(
        body.pipeline_voice_config,
        user_id,
        access,
        storage,
    )
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

    Each entry bundles what the chat UI needs to render without
    follow-up requests: the session record, its status, and — when
    the session participates in a team — the resolved team detail
    (leader agent + member agents with their session ids).

    The record arrives trimmed: ``state.context``, ``state.summary``
    and ``state.tool_context`` are cleared, since they hold the model's
    conversation and every file it has read. Fetch a session's messages
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
                    access,
                    user_id,
                    team_record,
                )
        is_running = await message_bus.is_locked(
            MessageBusKeys.session_lock(session.id),
        )
        # Derived before the trim below, which drops the context it reads.
        session_status = (
            SessionStatus.RUNNING
            if is_running
            else SessionService.derive_parked_status(session.state.context)
        )
        views.append(
            SessionView(
                # ``context`` is the conversation the model sees,
                # ``summary`` its compressed form, and ``tool_context``
                # caches every file read — megabytes a session, none of
                # which a sidebar renders.
                session=session.model_copy(
                    update={
                        "state": session.state.model_copy(
                            update={
                                "context": [],
                                "summary": "",
                                "tool_context": ToolContext(),
                            },
                        ),
                    },
                ),
                is_running=is_running,
                status=session_status,
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

    await _validate_session_config(body, user_id, access, storage)

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
            pipeline_voice_config=body.pipeline_voice_config,
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


@session_router.get("/{session_id}/inputs/{message_id}/result")
async def get_input_result(
    session_id: str,
    message_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    submission_id: str | None = Query(None, min_length=1),
    user_id: str = Depends(get_current_user_id),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict:
    """Resolve a submitted input using durable execution receipts."""
    try:
        return await chat_service.input_result(
            user_id,
            session_id,
            agent_id,
            message_id,
            submission_id=submission_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "session_not_found", "message": str(exc)},
        ) from exc


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
    reply_id: str
    | None = Query(
        None,
        min_length=1,
        description="Only interrupt this reply; ignore a stale target.",
    ),
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
        reply_id: Optional target reply; later replies must remain untouched.
        user_id: Injected authenticated user id.
        chat_service: Injected chat service.

    Returns:
        202 with :class:`InterruptSessionResponse` echoing the
        session id.

    Raises:
        HTTPException: 404 if the session does not exist.
    """
    try:
        await chat_service.interrupt(
            user_id,
            session_id,
            agent_id,
            reply_id=reply_id,
        )
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
    message_bus: MessageBus = Depends(get_message_bus),
) -> SessionRecord:
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
        `SessionRecord`: The full session record after the update.

    Raises:
        `HTTPException`: 404 if the session does not exist, or if the
            referenced credential / KB is not visible to the caller;
            409 if a chat run is currently active on the session.
    """
    async with message_bus.acquire_lock(
        MessageBusKeys.session_config_lock(session_id),
    ):
        return await _patch_session_config(
            session_id,
            body,
            agent_id,
            user_id,
            storage,
            access,
            message_bus,
        )


async def _patch_session_config(
    session_id: str,
    body: UpdateSessionRequest,
    agent_id: str,
    user_id: str,
    storage: StorageBase,
    access: ResourceAccessService,
    message_bus: MessageBus,
) -> SessionRecord:
    """Merge a PATCH into the latest record under the config lock."""
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    # Checked after the 404 so a missing session reports as missing, and
    # before the validation round trips below so we fail fast.
    if await message_bus.is_locked(MessageBusKeys.session_lock(session_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot modify session configuration while the session "
                "is running."
            ),
        )

    await _validate_session_config(body, user_id, access, storage)

    # ``None`` leaves the stored state untouched (see
    # ``StorageBase.upsert_session``). ``permission_mode`` is the only
    # request field living inside ``state``; every other field is a
    # pure config write, and rewriting ``state`` for those would put
    # this handler's opening snapshot back over whatever the run has
    # persisted since.
    updated_state = None
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
    current_voice = existing.config.pipeline_voice_config
    if body.pipeline_voice_config is not None and current_voice is not None:
        config_updates["pipeline_voice_config"] = {
            **current_voice.model_dump(),
            **config_updates["pipeline_voice_config"],
        }

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
        agent_id=agent_id,
        config=updated_config,
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
            MessageBusKeys.session_lock(session_id),
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


def _subscribe_session_events(
    bus: MessageBus,
    key: str,
    on_ready: Callable[[], None],
    on_barrier: RegisterSubscriptionBarrier,
) -> AsyncGenerator[dict, None]:
    """Subscribe with an ordered barrier when the backend accepts it."""
    subscribe = bus.subscribe
    try:
        inspect.signature(subscribe).bind(
            key,
            on_ready=on_ready,
            on_barrier=on_barrier,
        )
    except (TypeError, ValueError):
        return subscribe(key, on_ready=on_ready)
    return subscribe(key, on_ready=on_ready, on_barrier=on_barrier)


class _SessionBarrierTimeout(TimeoutError):
    """The subscription could not confirm delivery before replay."""


class _LegacySessionBarrier:
    """Confirm FIFO delivery on a readiness-only session subscription."""

    def __init__(self, bus: MessageBus, key: str) -> None:
        self.bus = bus
        self.key = key
        self.token: str | None = None
        self.received = asyncio.Event()

    async def flush(self) -> None:
        """Receive our own live marker after preceding session events."""
        self.token = uuid4().hex
        self.received.clear()
        marker = {MessageBusKeys.SESSION_BARRIER_FIELD: self.token}
        for _ in range(3):
            try:
                async with asyncio.timeout(_HEARTBEAT_INTERVAL_SECS):
                    await self.bus.publish(self.key, marker)
                    await self.received.wait()
                return
            except asyncio.TimeoutError:
                # A best-effort broadcast may drop a marker. Retry this
                # flush with the same token so either copy can confirm it.
                continue
        raise _SessionBarrierTimeout

    def consume(self, event: dict) -> bool:
        """Hide all markers and acknowledge only the current local flush."""
        if MessageBusKeys.SESSION_BARRIER_FIELD not in event:
            return False
        if event[MessageBusKeys.SESSION_BARRIER_FIELD] == self.token:
            self.received.set()
        return True


class _SessionEventReplay:
    """Merge the append-ordered log and live events using opaque cursors."""

    def __init__(self, bus: MessageBus, key: str, after: str | None) -> None:
        self.bus = bus
        self.key = key
        self.cursor = after
        self.pending: OrderedDict[str, dict] = OrderedDict()
        self.recent: OrderedDict[str, None] = OrderedDict()
        self.queued: set[str] = set()

    async def read(self) -> list[tuple[str, dict]]:
        """Extend the buffered log after its tail or the delivered cursor."""
        rows = await self.bus.log_read(
            self.key,
            since=next(reversed(self.pending), self.cursor),
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        )
        self.pending.update(rows)
        return rows

    async def read_initial(self) -> None:
        """Read all pages up to the live subscription or the end of the log."""
        rows = await self.read()
        while rows and rows[-1][0] not in self.queued:
            rows = await self.read()

    async def history_status(self, session_id: str) -> str:
        """Check the durable cursor after buffering the initial replay."""
        full = len(self.pending) >= MessageBusKeys.SESSION_REPLAY_MAX_LEN
        running = full and await self.bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        )
        cursor = await self.bus.registry_get(
            MessageBusKeys.session_event_checkpoint(session_id),
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
        )
        if cursor != self.cursor:
            return "changed"
        # A full log can start mid-reply. The next checkpoint contains the
        # missing prefix, while the retained suffix alone cannot restore it.
        return "pending" if running else "current"

    def advance(self, entry_id: str) -> None:
        """Retain recent IDs for late notifications after a checkpoint trim."""
        if self.cursor is not None:
            self.recent[self.cursor] = None
            if len(self.recent) > MessageBusKeys.SESSION_REPLAY_MAX_LEN:
                self.recent.popitem(last=False)
        self.cursor = entry_id

    async def _locate(self, entry_id: str) -> str | None:
        """Buffer the target or find its first surviving log successor."""
        if entry_id in self.pending:
            return None
        rows = await self.read()
        if entry_id in self.pending:
            return None
        following = await self.bus.log_read(
            self.key,
            since=entry_id,
            max_count=1,
        )
        successor = following[0][0] if following else None
        if not rows and successor is None:
            return None
        while entry_id not in self.pending and successor not in self.pending:
            if not await self.read():
                break
        return successor

    async def deliver(self, item: dict) -> AsyncGenerator[dict, None]:
        """Yield through the notified ID, including any missed broadcasts."""
        entry_id = item.get("_entry_id")
        if entry_id is None:
            yield item
            return
        if entry_id == self.cursor or entry_id in self.recent:
            return

        if (
            not self.pending
            and self.cursor is not None
            and item.get("_previous_entry_id") == self.cursor
        ):
            self.advance(entry_id)
            yield session_event_payload(item)
            return
        successor = await self._locate(entry_id)
        trimmed = entry_id not in self.pending
        if trimmed and self.cursor is not None:
            older = self.bus.log_cursor_precedes(entry_id, self.cursor)
            if older or (
                older is None
                and successor is not None
                and successor not in self.pending
            ):
                return
        while self.pending:
            first = next(iter(self.pending), entry_id)
            # A second trim can remove the snapshot's records as well.
            # Their queued live copies still establish their order.
            if trimmed:
                precedes = self.bus.log_cursor_precedes(entry_id, first)
                if precedes or (
                    precedes is None
                    and (first == successor or first in self.queued)
                ):
                    break
            key, payload = self.pending.popitem(last=False)
            self.advance(key)
            yield payload
            if key == entry_id:
                return
        # A checkpoint trimmed this event. Use its live payload before
        # delivering any retained successors.
        self.advance(entry_id)
        yield session_event_payload(item)


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
    after: str
    | None = Query(
        None,
        description=("Replay only events after this checkpoint cursor."),
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
    replay_notice: bool = False,
    validate_history: bool = False,
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
        replay_notice (`bool`):
            Send an SSE comment when the initial replay is complete.
        validate_history (`bool`):
            Verify the client's history cursor, including an empty cursor.

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
        legacy_barrier = _LegacySessionBarrier(message_bus, events_key)
        flush: Callable[[], Awaitable[None]] = legacy_barrier.flush
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        replay = _SessionEventReplay(message_bus, events_key, after)

        async def deliver(item: dict) -> AsyncGenerator[str, None]:
            if (entry_id := item.get("_entry_id")) is not None:
                replay.queued.discard(entry_id)
            async for payload in replay.deliver(item):
                data = json.dumps(payload, ensure_ascii=False)
                yield f"data: {data}\n\n"

        async def replay_snapshot(
            *,
            notify: bool = False,
        ) -> AsyncGenerator[str, None]:
            """Merge a fixed log snapshot with pending live notifications."""
            last_id = next(reversed(replay.pending), None)
            if last_id is None and not notify:
                return
            # The subscription can still hold trimmed frames even when the
            # feeder queue is empty. Its ordered marker covers both buffers.
            flushing = asyncio.ensure_future(flush())
            try:
                while not flushing.done():
                    done, _ = await asyncio.wait(
                        (flushing, feeder_task),
                        timeout=_HEARTBEAT_INTERVAL_SECS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if feeder_task in done and not flushing.done():
                        return
                    if not done:
                        yield ": replay-pending\n\n" if notify else ":\n\n"
                try:
                    await flushing
                except TimeoutError as exc:
                    raise _SessionBarrierTimeout from exc
            finally:
                flushing.cancel()
                await asyncio.gather(flushing, return_exceptions=True)
            for _ in range(queue.qsize()):
                item = queue.get_nowait()
                if item is None:
                    queue.put_nowait(None)
                    break
                async for frame in deliver(item):
                    yield frame
            if last_id is not None and last_id in replay.pending:
                async for frame in deliver({"_entry_id": last_id}):
                    yield frame
            if notify and not feeder_task.done():
                yield ": replay-complete\n\n"

        def register_barrier(callback: Callable[[], Awaitable[None]]) -> None:
            nonlocal flush
            flush = callback
            ready.set()

        async def _feeder() -> None:
            """Read from the bus subscription and forward to the queue.

            Pushes ``None`` as a sentinel when the subscription ends
            (which in practice only happens if the bus shuts down).
            """
            try:
                async for evt in _subscribe_session_events(
                    message_bus,
                    events_key,
                    ready.set,
                    register_barrier,
                ):
                    if legacy_barrier.consume(evt):
                        continue
                    if (entry_id := evt.get("_entry_id")) is not None:
                        replay.queued.add(entry_id)
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
            await replay.read_initial()
            if validate_history:
                history = await replay.history_status(session_id)
                yield f": history-{history}\n\n"
                if history != "current":
                    return
            async for frame in replay_snapshot(notify=replay_notice):
                yield frame

            # Inject pending subagent HITL cards projected onto this
            # session as a team leader. These live in a durable registry,
            # outside the replay log.
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

            loop = asyncio.get_running_loop()
            next_check = loop.time() + _HEARTBEAT_INTERVAL_SECS
            while True:
                remaining = next_check - loop.time()
                if not queue.empty():
                    # Already queued frames may have been trimmed from the log.
                    item = queue.get_nowait()
                elif remaining > 0:
                    try:
                        item = await asyncio.wait_for(queue.get(), remaining)
                    except asyncio.TimeoutError:
                        continue
                else:
                    await replay.read()
                    async for frame in replay_snapshot():
                        yield frame
                    next_check = loop.time() + _HEARTBEAT_INTERVAL_SECS
                    yield ":\n\n"
                    continue
                if item is None:
                    break
                async for frame in deliver(item):
                    yield frame
        except _SessionBarrierTimeout:
            # Close without advancing past unconfirmed live events.
            return
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
