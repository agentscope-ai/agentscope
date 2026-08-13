# -*- coding: utf-8 -*-
"""Agent router — CRUD endpoints for agent configurations."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from ...agent import ContextConfig, ReActConfig
from ..._utils._common import _flatten_json_schema
from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_session_service,
    get_storage,
)
from ._schema import (
    AgentSchemaResponse,
    AgentSchemaV2Response,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from .._service import AgentView, ResourceAccessService, SessionService
from ..storage import (
    StorageBase,
    AgentData,
    AgentRecord,
    InviteConfig,
    TeamConfig,
    HandoffRelation,
)

agent_router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={404: {"description": "Not found"}},
)


@agent_router.get(
    "/schema",
    response_model=AgentSchemaResponse,
    deprecated=True,
    summary="[Deprecated] Legacy sectioned schema — use /schema/v2",
)
async def get_agent_schema() -> AgentSchemaResponse:
    """Return the legacy sectioned JSON Schema fragments.

    .. deprecated::
        Superseded by :func:`get_agent_schema_v2`, which returns the
        full :class:`AgentData` schema in a single ``schema`` field.
        Kept for backwards compatibility with existing API consumers.
        New consumers should call ``GET /agent/schema/v2``.

    The frontend previously used three sections — identity, context
    config, and react config — so we return them as separate
    self-contained schemas rather than a single :class:`AgentData`
    schema with ``$ref`` s.

    Returns:
        `AgentSchemaResponse`:
            Schemas for the three form sections.
    """
    # Slice ``AgentData``'s schema down to the identity-relevant fields.
    # Going through ``AgentData.model_json_schema()`` (rather than building
    # a dict by hand) keeps Pydantic as the single source of truth for
    # defaults, titles, descriptions, and the ``format: textarea`` hint.
    agent_schema = AgentData.model_json_schema()
    identity_keys = ("name", "system_prompt")
    identity = {
        "type": "object",
        "title": "Identity",
        "properties": {
            k: v
            for k, v in agent_schema.get("properties", {}).items()
            if k in identity_keys
        },
        "required": [
            r for r in agent_schema.get("required", []) if r in identity_keys
        ],
    }

    context_schema = ContextConfig.model_json_schema()
    # ``summary_schema`` holds a Pydantic JSON Schema describing how the
    # compression model should structure its output. The end-user is not
    # expected to edit it from the form, so we hide it.
    context_schema.get("properties", {}).pop("summary_schema", None)

    return AgentSchemaResponse(
        identity=identity,
        context_config=context_schema,
        react_config=ReActConfig.model_json_schema(),
    )


@agent_router.get(
    "/schema/v2",
    response_model=AgentSchemaV2Response,
    summary="Full AgentData JSON Schema for the agent form",
)
async def get_agent_schema_v2() -> AgentSchemaV2Response:
    """Return the full :class:`AgentData` JSON Schema.

    Superset of the legacy sectioned endpoint. The response body is a
    single ``schema`` field carrying the whole Pydantic-generated
    schema of :class:`AgentData`, with two curated exclusions handled
    at the model layer (so no post-processing is needed here):

    - ``id``: server-assigned, marked :class:`SkipJsonSchema` on
      :attr:`AgentData.id`.
    - ``context_config.summary_schema``: internal structured-output
      spec for the compression model, dropped below since it is not
      user-editable and there is no equivalent hook on the Pydantic
      side.

    ``$ref`` inlining is delegated to
    :func:`~agentscope._utils._common._flatten_json_schema` so the
    frontend can render every property from the response body alone.

    The frontend derives its section grouping (identity / context /
    react / invite) directly from this schema — top-level scalar
    properties are the "identity" section, and top-level nested-object
    properties each become their own section. Adding a new
    user-editable field to :class:`AgentData` is thus enough to have it
    appear in the create / edit form without a router change.

    Returns:
        `AgentSchemaV2Response`:
            ``schema`` = the full :class:`AgentData` JSON Schema.
    """
    schema = _flatten_json_schema(AgentData.model_json_schema())
    # ``summary_schema`` is Pydantic's structured-output spec fed to the
    # compression model — internal, not user-editable. No pydantic-side
    # hook covers this deep nested field, so drop it after inlining.
    context_config = schema.get("properties", {}).get("context_config", {})
    context_config.get("properties", {}).pop("summary_schema", None)
    return AgentSchemaV2Response(schema=schema)


@agent_router.get(
    "/",
    response_model=ListAgentsResponse,
    summary="List all agents",
)
async def list_agents(
    parent_agent_id: str | None = Query(
        default=None,
        description=(
            "When set, only return members of the referenced team leader. "
            "When omitted, team members are hidden so the top-level agent "
            "list stays clean."
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListAgentsResponse:
    """Return all agent records visible to the authenticated user.

    Includes the caller's own ``source == "user"`` agents plus any agents
    shared to them through :class:`ResourceAccessPolicyBase`. Each entry
    carries an ``editable`` flag indicating whether the caller may
    PATCH/DELETE it, and an ``is_team`` flag marking expert-team leaders.

    Pass ``parent_agent_id`` to list the members of a specific expert team
    (otherwise team members are hidden so the top-level list stays clean).

    Args:
        parent_agent_id (`str | None`):
            Optional team leader id to filter members.
        user_id (`str`):
            Injected authenticated user ID.
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ListAgentsResponse`:
            All visible agent records paired with per-viewer editability.
    """
    entries = await access.list_resource(
        user_id,
        ResourceKind.AGENT,
        parent_agent_id=parent_agent_id,
    )
    return ListAgentsResponse(agents=entries, total=len(entries))


@agent_router.post(
    "/",
    response_model=CreateAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent",
)
async def create_agent(
    body: CreateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> CreateAgentResponse:
    """Create and persist a new agent configuration.

    When ``body.parent_agent_id`` is set, the new agent is created as a
    member of that leader's expert team: its ``data.parent_agent_id`` is
    stamped and the leader's ``team_config.member_ids`` is extended with
    the new agent id (creating the leader's team_config if absent, and
    honoring ``max_members``).

    Args:
        body (`CreateAgentRequest`):
            Agent configuration to store.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.
        access (`ResourceAccessService`):
            Injected resource access service (used to build the view and
            to join the parent team).

    Returns:
        `CreateAgentResponse`:
            The server-assigned agent identifier.

    Raises:
        `HTTPException`: 422 if the request body passes
            :class:`CreateAgentRequest` validation but the resulting
            :class:`AgentData` fails its cross-field invariants (e.g.
            ``invite_config.invitable=True`` without a non-empty
            ``invite_description``). Symmetrical with
            :func:`update_agent`.
        `HTTPException`: 404 if the referenced ``parent_agent_id`` does
            not exist.
        `HTTPException`: 409 if adding the member would exceed the
            leader's ``max_members``.
    """
    parent_id = body.parent_agent_id
    if parent_id is not None:
        leader = await storage.get_agent(user_id, parent_id)
        if leader is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent agent '{parent_id}' not found.",
            )
        if leader.data.team_config is None:
            leader.data.team_config = TeamConfig()
        cfg = leader.data.team_config
        if len(cfg.member_ids) >= cfg.max_members:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team already at max_members={cfg.max_members}.",
            )
        # Members created under a leader are automatically invitable so
        # the leader can ``AgentInvite`` them into an active team at
        # runtime — otherwise the persistent team config would be
        # unreachable from the workflow tools and the frontend would
        # have to remember to flip the toggle on every member.
        if not body.invite_config.invitable:
            body.invite_config.invitable = True
        if not body.invite_config.invite_description:
            body.invite_config.invite_description = (
                f"Member of team led by {leader.data.name}."
            )

    try:
        data = AgentData(
            name=body.name,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
            invite_config=body.invite_config,
            parent_agent_id=parent_id,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    record = AgentRecord(user_id=user_id, data=data)
    agent_id = await storage.upsert_agent(user_id, record)

    if parent_id is not None:
        leader = await storage.get_agent(user_id, parent_id)
        if leader.data.team_config is None:
            leader.data.team_config = TeamConfig()
        leader.data.team_config.member_ids.append(agent_id)
        await storage.upsert_agent(user_id, leader)

    return CreateAgentResponse(agent_id=agent_id)


@agent_router.patch(
    "/{agent_id}",
    response_model=AgentView,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> AgentView:
    """Partially update an existing agent configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        agent_id (`str`): The agent to update.
        body (`UpdateAgentRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `AgentView`: The full agent record after the update.

    Raises:
        `HTTPException`: 404 if the agent is not visible to the caller;
            403 if visible but only readable.
    """
    owner_id, existing = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )

    updates = body.model_dump(exclude_none=True)

    # Backstop for team members: an agent created as a member under a
    # leader (``parent_agent_id`` set) must stay invitable no matter
    # what the frontend sends. Without this, a PATCH that flips
    # ``invite_config.invitable`` off (or strips the description) would
    # silently orphan the member from the leader's ``AgentInvite`` pool
    # and break the assembled workflow chain later.
    if existing.data.parent_agent_id is not None:
        inv = updates.get("invite_config") or (
            existing.data.invite_config or InviteConfig()
        ).model_dump()
        inv = {**inv, "invitable": True}
        if not (inv.get("invite_description") or "").strip():
            leader = await storage.get_agent(
                owner_id,
                existing.data.parent_agent_id,
            )
            leader_name = (
                leader.data.name
                if leader is not None
                else existing.data.parent_agent_id
            )
            inv["invite_description"] = (
                f"Member of team led by {leader_name}."
            )
        updates["invite_config"] = inv

    # ``model_copy(update=...)`` skips validators; re-run
    # ``AgentData.model_validate`` on the merged shape so the
    # ``invite_config`` sub-model's ``invitable ⇒ non-empty description``
    # invariant enforced by ``@model_validator(mode="after")`` produces
    # an HTTP 422 instead of a stored-but-invalid record.
    try:
        updated_data = AgentData.model_validate(
            {**existing.data.model_dump(), **updates},
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    updated_agent = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()},
    )
    await storage.upsert_agent(owner_id, updated_agent)
    # Only reachable via ``resolve_for_edit``, so the caller has edit
    # permission by construction.
    return AgentView.model_validate(
        {**updated_agent.model_dump(), "editable": True},
    )


@agent_router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
)
async def delete_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> None:
    """Permanently delete an agent configuration.

    Cascades through every session owned by this agent (and, for team
    leaders, through every worker session) — cancelling any in-flight
    chat run, removing storage records, and purging bus state.

    Args:
        agent_id (`str`): The agent to delete.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.
        access (`ResourceAccessService`): Injected access service — used
            to resolve the owning user and enforce the edit permission
            when a shared editor deletes the agent.

    Raises:
        `HTTPException`: 404 if the agent is not visible to the caller;
            403 if visible but only readable.
    """
    owner_id, _ = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    deleted = await session_service.delete_agent(owner_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )


# ======================================================================
# Expert-team endpoints
# ----------------------------------------------------------------------
# A "team" is just a leader agent carrying a TeamConfig whose member_ids
# reference ordinary AgentRecords (members). These endpoints manage that
# config without reinventing agent CRUD: members are created/edited via
# POST/PATCH /agent and only linked/unlinked here. The config is consumed
# at session start (see app._service._chat) to seed the leader's runtime
# team and inject a collaboration briefing into its system prompt.
# ======================================================================


class TeamMemberView(BaseModel):
    """A member entry in a team config response, with denormalized core
    fields so the frontend can render the team without extra calls."""

    agent_id: str
    name: str
    description: str | None = None
    is_self_built: bool = Field(
        description=(
            "True when the member was created under this leader "
            "(parent_agent_id == leader). False when invited by reference "
            "from another owner (frozen config, not deletable here)."
        ),
    )


class TeamConfigResponse(BaseModel):
    """Full expert-team configuration plus resolved member details."""

    agent_id: str
    name: str
    is_team: bool = True
    collaboration_mode: str
    max_members: int
    handoff_relations: list[HandoffRelation]
    members: list[TeamMemberView]


class SetTeamConfigRequest(BaseModel):
    """Replace the leader's team configuration.

    member_ids may reference existing agents (self-built or invited). The
    leader's ``parent_agent_id`` backlinks of referenced self-built members
    are reconciled automatically.
    """

    collaboration_mode: Literal["free_handoff", "workflow"] = "free_handoff"
    member_ids: list[str] = Field(default_factory=list)
    handoff_relations: list[HandoffRelation] = Field(default_factory=list)
    max_members: int = 10


class AddMemberRequest(BaseModel):
    """Add a member to the team by reference (invite an existing agent)."""

    agent_id: str = Field(
        description="Existing agent ID to invite into this team.",
    )


class HandoffRelationResponse(BaseModel):
    handoff_relations: list[HandoffRelation]


class SetCollaborationModeRequest(BaseModel):
    """Switch the team's collaboration mode (soft vs hard constraint)."""

    collaboration_mode: Literal["free_handoff", "workflow"]


class CollaborationModeResponse(BaseModel):
    collaboration_mode: Literal["free_handoff", "workflow"]


# Force-rebuild the request model so Pydantic's TypeAdapter used by FastAPI
# can fully resolve ``Literal`` and the cross-module ``HandoffRelation``
# reference at module-import time. Without this, FastAPI raises
# ``PydanticUserError: ... is not fully defined`` when the route fires.
SetTeamConfigRequest.model_rebuild()


def _require_leader(agent: AgentRecord | None) -> AgentRecord:
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    return agent


@agent_router.get(
    "/{agent_id}/team/config",
    response_model=TeamConfigResponse,
    summary="Get expert-team configuration (with member details)",
)
async def get_team_config(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> TeamConfigResponse:
    """Return the full expert-team config for ``agent_id``.

    Lists each member (self-built or invited) with denormalized name and
    description. Raises 404 if the agent is not visible to the caller.
    The agent need not yet be a team (empty member_ids is reported).
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    cfg = agent.data.team_config or TeamConfig()
    members: list[TeamMemberView] = []
    for mid in cfg.member_ids:
        m = await storage.get_agent(owner_id, mid)
        if m is None:
            continue
        members.append(
            TeamMemberView(
                agent_id=m.id,
                name=m.data.name,
                description=m.data.invite_config.invite_description,
                is_self_built=(m.data.parent_agent_id == agent_id),
            )
        )
    return TeamConfigResponse(
        agent_id=agent.id,
        name=agent.data.name,
        is_team=bool(cfg.member_ids),
        collaboration_mode=cfg.collaboration_mode,
        max_members=cfg.max_members,
        handoff_relations=cfg.handoff_relations,
        members=members,
    )


@agent_router.put(
    "/{agent_id}/team/config",
    response_model=TeamConfigResponse,
    summary="Replace expert-team configuration",
)
async def set_team_config(
    agent_id: str,
    body: SetTeamConfigRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> TeamConfigResponse:
    """Replace the leader's team configuration wholesale.

    Reconciles ``parent_agent_id`` backlinks: each member whose id is in
    ``member_ids`` and is owned by the caller gets its ``parent_agent_id``
    set to this leader (establishing a self-built membership); members
    removed from the list have their backlink cleared (invited members are
    never deleted, only unlinked). Honors ``max_members``.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    if len(body.member_ids) > body.max_members:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"member_ids exceeds max_members={body.max_members}.",
        )
    # Reconcile self-built backlinks.
    all_agents = await storage.list_agents(owner_id)
    for m in all_agents:
        if m.id in body.member_ids and m.data.parent_agent_id != agent_id:
            m.data.parent_agent_id = agent_id
            await storage.upsert_agent(owner_id, m)
        elif (
            m.data.parent_agent_id == agent_id
            and m.id not in body.member_ids
        ):
            m.data.parent_agent_id = None
            await storage.upsert_agent(owner_id, m)

    agent.data.team_config = TeamConfig(
        collaboration_mode=body.collaboration_mode,
        member_ids=list(body.member_ids),
        handoff_relations=body.handoff_relations,
        max_members=body.max_members,
    )
    await storage.upsert_agent(owner_id, agent)
    return await get_team_config(agent_id, user_id, access, storage)


@agent_router.get(
    "/{agent_id}/team/mode",
    response_model=CollaborationModeResponse,
    summary="Get the team's collaboration mode",
)
async def get_collaboration_mode(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> CollaborationModeResponse:
    """Return the current collaboration mode — ``free_handoff`` (soft
    guidance) or ``workflow`` (hard ordering). Defaults to
    ``free_handoff`` for agents without a team config yet.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    cfg = agent.data.team_config
    return CollaborationModeResponse(
        collaboration_mode=(
            cfg.collaboration_mode if cfg is not None else "free_handoff"
        )
    )


@agent_router.put(
    "/{agent_id}/team/mode",
    response_model=CollaborationModeResponse,
    summary="Switch the team's collaboration mode",
)
async def set_collaboration_mode(
    agent_id: str,
    body: SetCollaborationModeRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> CollaborationModeResponse:
    """Toggle between soft guidance (``free_handoff``) and hard ordering
    (``workflow``) without touching members or handoff edges. A missing
    team config is auto-created with the requested mode.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    cfg = agent.data.team_config
    if cfg is None:
        cfg = TeamConfig()
        agent.data.team_config = cfg
    cfg.collaboration_mode = body.collaboration_mode
    await storage.upsert_agent(owner_id, agent)
    return CollaborationModeResponse(collaboration_mode=cfg.collaboration_mode)


@agent_router.post(
    "/{agent_id}/team/members",
    response_model=TeamConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Add a member to the team by reference",
)
async def add_team_member(
    agent_id: str,
    body: AddMemberRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> TeamConfigResponse:
    """Invite an existing agent (``body.agent_id``) into the team.

    Appends to ``member_ids`` if not already present and within
    ``max_members``. The invited agent's own config is frozen and is not
    modified here.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    if agent.data.team_config is None:
        agent.data.team_config = TeamConfig()
    cfg = agent.data.team_config
    if body.agent_id in cfg.member_ids:
        return await get_team_config(agent_id, user_id, access, storage)
    if len(cfg.member_ids) >= cfg.max_members:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Team already at max_members={cfg.max_members}.",
        )
    cfg.member_ids.append(body.agent_id)
    await storage.upsert_agent(owner_id, agent)
    return await get_team_config(agent_id, user_id, access, storage)


@agent_router.delete(
    "/{agent_id}/team/members/{member_id}",
    response_model=TeamConfigResponse,
    summary="Remove a member from the team",
)
async def remove_team_member(
    agent_id: str,
    member_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
    session_service: SessionService = Depends(get_session_service),
) -> TeamConfigResponse:
    """Unlink ``member_id`` from the team.

    If the member was self-built (``parent_agent_id == leader``) it is
    cascade-deleted; if it was invited by reference, only the link is
    removed (the underlying agent is preserved, per the permission
    isolation rule). The removed member is also detached from any
    handoff_relations on this team.

    Borrowed-session cleanup
        An invited member's team conversation lives in a
        ``team:<leader_team_id>/invited:<handle>``-named session
        whose ``team_id`` references this team's roster. If we
        leave that session behind, two things break later:

        1. The member's primary (user-owned) session is fine, but
           the ghost team session keeps a live inbox queue and the
           wakeup dispatcher will keep trying to wake it — every
           leader-side ``TeamSay`` with a ``to=<this member>`` will
           bleed into a stale session that no longer appears in
           the roster.
        2. Re-inviting the same member in the future fails the
           Duplicate-borrow guard inside :class:`AgentInvite`
           because the previous borrow is still alive.

        We therefore enumerate all sessions for ``member_id`` and
        delete any whose ``team_id`` matches this team's roster
        id. Self-built members are handled by
        :meth:`SessionService.delete_agent` which already cascades
        sessions; we still call the borrow cleanup afterwards to
        be defensive in case a past bug left a leak.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    if agent.data.team_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent is not a team leader.",
        )
    cfg = agent.data.team_config
    if member_id not in cfg.member_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member '{member_id}' not in this team.",
        )

    # 1. Sweep the member's borrowed team sessions *first*, while we
    #    still know ``member_id`` is in the roster. Borrowed session
    #    names start with the ``"team:"`` prefix per
    #    :class:`AgentInvite`'s naming convention, which is a stable
    #    internal invariant — user-owned primary sessions never use
    #    that prefix. We rely on the prefix instead of an explicit
    #    ``team_id`` lookup because the leader's roster may reference
    #    the same member under multiple historical team ids (a stale
    #    invite failure or a previously-deleted team) and we want to
    #    clean every ghost, not just the most recent one.
    member_sessions = await storage.list_sessions(owner_id, member_id)
    for s in member_sessions:
        if (s.config.name or "").startswith("team:"):
            await session_service.delete_session(
                owner_id,
                member_id,
                s.id,
            )

    cfg.member_ids.remove(member_id)
    cfg.handoff_relations = [
        r
        for r in cfg.handoff_relations
        if r.from_agent_id != member_id and r.to_agent_id != member_id
    ]

    # 3. If the member was self-built, drop its underlying agent
    #    (which also cascades its remaining sessions and the
    #    agent index entry). If the member was invited by
    #    reference, leave the agent in place — the user owns it
    #    independently of this team.
    member = await storage.get_agent(owner_id, member_id)
    if member is not None and member.data.parent_agent_id == agent_id:
        await session_service.delete_agent(owner_id, member_id)
    await storage.upsert_agent(owner_id, agent)
    return await get_team_config(agent_id, user_id, access, storage)


@agent_router.get(
    "/{agent_id}/team/handoff",
    response_model=HandoffRelationResponse,
    summary="Get team handoff relations",
)
async def get_handoff(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> HandoffRelationResponse:
    """Return the leader's handoff relations (collaboration order)."""
    _, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    cfg = agent.data.team_config or TeamConfig()
    return HandoffRelationResponse(handoff_relations=cfg.handoff_relations)


@agent_router.put(
    "/{agent_id}/team/handoff",
    response_model=HandoffRelationResponse,
    summary="Replace team handoff relations",
)
async def set_handoff(
    agent_id: str,
    body: HandoffRelationResponse,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> HandoffRelationResponse:
    """Replace the leader's handoff relations.

    Each relation's endpoints must reference the leader or one of its
    current members; otherwise 422.

    The edges are *soft* by default: when ``collaboration_mode`` is
    unset (defaults to ``free_handoff``) the relations are injected
    into the leader's system prompt as delegation-order guidance. If
    the team is later switched to ``collaboration_mode == "workflow"``
    the same edges become a hard ordered chain enforced at runtime by
    the toolkit layer (``allowed_handoff_targets``). No mode switch is
    required to store the relations.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    if agent.data.team_config is None:
        agent.data.team_config = TeamConfig()
    cfg = agent.data.team_config
    allowed = {agent_id, *cfg.member_ids}
    for r in body.handoff_relations:
        if r.from_agent_id not in allowed or r.to_agent_id not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Handoff endpoints must reference the leader or a "
                    f"current member: '{r.from_agent_id}' -> "
                    f"'{r.to_agent_id}'."
                ),
            )
    cfg.handoff_relations = list(body.handoff_relations)
    await storage.upsert_agent(owner_id, agent)
    return HandoffRelationResponse(handoff_relations=cfg.handoff_relations)
