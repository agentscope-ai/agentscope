# -*- coding: utf-8 -*-
"""BocomADP agent router — CRUD + expert-team endpoints.

Moved out of ``src/agentscope/app/_router/_agent.py`` per the team rule
that framework sources stay untouched: the framework router is detached
in ``main.py`` and this router (including the 8 ``/team/*`` endpoints
and the CRUD expert-team behavior) is registered instead.
"""
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from agentscope.agent import ContextConfig, ReActConfig
from agentscope._utils._common import _flatten_json_schema, _generate_id
from agentscope.app.access import ResourceKind
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_session_service,
    get_storage,
)
from bocomadp.routers._schema.agent import (
    AgentSchemaResponse,
    AgentSchemaV2Response,
    ListAgentsResponse,
    ListOwnedAgentsResponse,
    OwnedAgentView,
    PublishInfoView,
    CreateAgentRequest,
    CreateAgentResponse,
    CopyAgentRequest,
    CopyAgentResponse,
    UpdateAgentRequest,
    TeamAgentView,
)
from agentscope.app._service import ResourceAccessService, SessionService
from agentscope.app.storage import (
    StorageBase,
    AgentData,
    AgentRecord,
    InviteConfig,
)
from bocomadp.agent_template_store import get_template_entry
from bocomadp.agents_extend_store import (
    delete_agent_extend,
    ensure_agent_extend_row,
)
from bocomadp.team_store import (
    ExpertTeamRelation,
    HandoffRelation,
    get_team,
    list_teams,
    upsert_team,
)

agent_router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger("bocomadp.agent")


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
    identity_keys = ("name", "description", "system_prompt")
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


async def _attach_publish_status(
    storage: StorageBase,
    items: list[TeamAgentView],
) -> None:
    """批量给视图附上发布/审批状态（in-place）。

    与 ``GET /agent/owned`` 的 ``publish_status`` 同口径（两步小查询，
    不逐条查）：

    - 已在市场（``agent_market`` 有行，含平台内置手动上架）→ approved；
    - 有审批记录 → pending / rejected（审批结论走同项的 ``review_reason``）；
    - 都没有 → 保持默认 not_submitted（未发布/待发布）。

    同时回显**发布档案四件套**（department / system_name / tag /
    description）：用户点发布时填的表单原值，前端在"已驳回重新发布"
    时直接回填弹窗，省得重填。口径：**有档案就带**
    ——approved 取市场行（上架即定格），pending/rejected 取审批记录，
    not_submitted 保持空串。数据来源都是本函数已有的两次批量查询，
    **不新增查询次数**。

    同时附带**审批明细**（review_reason / reviewer / reviewed_at）：
    前端不用再单查发布状态接口，一次列表请求即可渲染状态角标、驳回
    理由与"谁在什么时候审的"。口径同"有档案就带"：审批记录存在就带，
    下架后记录已清除则回落空值。数据同样来自已有的两次批量查询，
    **不新增查询次数**。

    调用方：``GET /agent/``（分页后的当前页批量附带）与
    ``_to_team_view``（PATCH 单条返回附带）。
    """
    if not items:
        return
    from bocomadp.market_review_store import STATUS_APPROVED, STATUS_REJECTED
    from bocomadp.market_review_store import review_status_map
    from bocomadp.market_store import list_market_entries

    ids = [item.id for item in items]
    status_map = await review_status_map(storage, ids)
    market_map = {
        e.agent_id: e
        for e in await list_market_entries(storage)
        if e.agent_id in set(ids)
    }
    for item in items:
        review = status_map.get(item.id)
        entry = market_map.get(item.id)
        if entry is not None:
            item.publish_status = STATUS_APPROVED
            _fill_publish_archive(
                item,
                entry.department,
                entry.system_name,
                entry.tag,
                entry.description,
            )
        elif review is None:
            continue  # 默认 not_submitted（schema 默认值）
        else:
            item.publish_status = review.status
            _fill_publish_archive(
                item,
                review.department,
                review.system_name,
                review.tag,
                review.description,
            )
        if review is None:
            continue
        if review.status in (STATUS_APPROVED, STATUS_REJECTED):
            item.review_reason = review.reason
        item.reviewer = review.reviewer
        item.reviewed_at = review.reviewed_at


def _fill_publish_archive(
    item: TeamAgentView | OwnedAgentView,
    department: str,
    system_name: str,
    tag: str,
    description: str,
) -> None:
    """把发布档案四件套写进 ``item.publish_info``（空值兜底空串，回显用）。

    包在 ``publish_info`` 对象里而不是摊平到顶层：``data.description``
    是"智能体简介"，这里的 ``description`` 是"发布说明"，同名不同义。
    """
    item.publish_info = PublishInfoView(
        department=department or "",
        system_name=system_name or "",
        tag=tag or "",
        description=description or "",
    )


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
    page_num: int = Query(
        default=1,
        ge=1,
        alias="pageNum",
        description="Page number, 1-based.",
    ),
    page_size: int = Query(
        default=5,
        ge=1,
        le=100,
        alias="pageSize",
        description="Page size (items per page), 1-100.",
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    is_team: bool | None = Query(
        default=None,
        description=(
            "Optional top-level filter: `true` returns only expert-team "
            "leaders, `false` only plain agents. Omit to list all."
        ),
    ),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListAgentsResponse:
    """Return all agent records visible to the authenticated user.

    Includes the caller's own ``source == "user"`` agents plus any agents
    shared to them through :class:`ResourceAccessPolicyBase`. Each entry
    carries an ``editable`` flag indicating whether the caller may
    PATCH/DELETE it, and an ``is_team`` flag marking expert-team leaders
    (``is_team=true`` filters to leaders only; ``is_team=false`` to plain
    agents).

    Pass ``parent_agent_id`` to list the members of a specific expert team
    (otherwise team members are hidden so the top-level list stays clean).

    Args:
        parent_agent_id (`str | None`):
            Optional team leader id to filter members.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend (used to look up the team roster when
            ``parent_agent_id`` is given).
        access (`ResourceAccessService`):
            Injected resource access service.

    Returns:
        `ListAgentsResponse`:
            All visible agent records paired with per-viewer editability.
    """
    # 生产环境已由 main.py 调用 patch_team_access()/patch_agent_list_sort()，
    # 此时 access.list_resource 签名是 (viewer_id, kind, parent_agent_id=None)：
    # 传 parent_agent_id 走成员分支返回名册，不传走顶层分支（隐藏自建成员、
    # 保留被邀成员 + is_team 标记）。必须把 parent_agent_id 透传过去，
    # 否则真实环境永远走顶层分支，带 parent 的名册查询就缺自建成员。
    # 未 patch 的环境（纯单元测试）签名是 (viewer_id, kind)，透传会
    # TypeError，此时回退到自行按团队关系表过滤（行为等价）。
    try:
        entries = await access.list_resource(
            user_id,
            ResourceKind.AGENT,
            parent_agent_id=parent_agent_id,
        )
    except TypeError:
        entries = await access.list_resource(user_id, ResourceKind.AGENT)
        if parent_agent_id is not None:
            # The framework access layer has no team concept; member filtering
            # is re-derived from the expert-team relation table here.
            team = await get_team(storage, user_id, parent_agent_id)
            member_ids = set(team.member_ids) if team is not None else set()
            entries = [e for e in entries if e.id in member_ids]
        else:
            # Top-level list stays clean: *self-built* team members are hidden
            # here and reachable only via parent_agent_id=<leader> (matches
            # the docstring above, docs/expert-team-api.md, and the
            # access-layer patch in team_access.py). Under scheme B there are
            # no invited-by-reference members, so this set is exactly the
            # self-built membership.
            teams = await list_teams(storage, user_id)
            member_ids = {
                m.agent_id
                for team in teams
                for m in team.members
                if team.is_self_built(m.agent_id)
            }
            if member_ids:
                entries = [e for e in entries if e.id not in member_ids]
    # is_team 筛选：生产环境顶层分支已在 TeamAgentView 上标记 is_team
    # （True=团长，False=普通成员）。未 patch 的环境没有该字段，
    # getattr 兜底为 None，此时两种过滤都筛空（语义合理：无团队概念）。
    if is_team is not None:
        entries = [e for e in entries if getattr(e, "is_team", None) is is_team]
    # 分页：entries 已按 updated_at 倒序（框架 list_resource 的排序逻辑），
    # 直接切片即可，total 用切片前的完整数量，前端可据此算总页数。
    total = len(entries)
    start = (page_num - 1) * page_size
    page_entries = entries[start : start + page_size]
    # 框架 list_resource 返回 AgentView，而 schema 需要 TeamAgentView
    # （多出 is_team / parent_agent_id / is_self_built 三个专家团字段），
    # 显式转换以通过 Pydantic 校验。
    views = [TeamAgentView(**e.model_dump()) for e in page_entries]
    # 发布/审批状态批量附带（当前页，与 /agent/owned 同口径）
    await _attach_publish_status(storage, views)
    return ListAgentsResponse(agents=views, total=total)


@agent_router.get(
    "/owned",
    response_model=ListOwnedAgentsResponse,
    summary="List agents strictly owned by the caller (user isolation)",
)
async def list_owned_agents(
    page_num: int = Query(
        default=1,
        ge=1,
        alias="pageNum",
        description="Page number, 1-based.",
    ),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        alias="pageSize",
        description="Page size (items per page), 1-100.",
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListOwnedAgentsResponse:
    """返回**本人名下拥有**的智能体清单（严格用户隔离）。

    与 ``GET /agent/``（可见性视图）的差异：

    - 只含 ``user_id=调用者 AND source='user'`` 的记录——别人**共享
      给我**的智能体不出现在这里（那是可见性，不是所有权）；
    - ``source='team'`` 的派生 worker 永不出现（不是用户创建的资产）；
    - **自建成员不返回**：成员独属其团（``expert_team_relations``），
      不与团长/独立智能体并列；成员明细走
      ``GET /agent/?parent_agent_id={团长id}`` 名册接口。
      ``parent_agent_id`` / ``is_self_built`` 字段保留但恒为 ``null``
      （响应结构不变，前端按 ``is_team`` 渲染团卡片即可）。

    Args:
        page_num (`int`): 页码，1-based。
        page_size (`int`): 每页条数，1-100。
        user_id (`str`): 注入的登录用户 ID（X-User-ID 头）。
        storage (`StorageBase`): 注入的存储后端。

    Returns:
        `ListOwnedAgentsResponse`: 按 ``updated_at`` 倒序的归属清单。
    """
    records = await storage.list_agents(user_id)
    teams = await list_teams(storage, user_id)
    # updated_at 倒序（与 GET /agent/ 的展示习惯一致）；团队标记一次
    # list_teams 全量算好，避免每条记录各查一遍关系表。
    # 自建成员独属其团，不在归属清单里与团长/独立智能体并列——
    # 前端要成员明细走 GET /agent/?parent_agent_id={团长id}。
    member_ids: set[str] = {
        m for t in teams for m in t.member_ids  # noqa: C416
    }
    items: list[OwnedAgentView] = []
    owned_ids: list[str] = []
    for record in sorted(records, key=lambda r: r.updated_at, reverse=True):
        if record.id in member_ids:
            continue
        owned_ids.append(record.id)
        items.append(
            OwnedAgentView(
                id=record.id,
                name=record.data.name,
                description=record.data.description,
                system_prompt=record.data.system_prompt,
                is_team=any(t.leader_agent_id == record.id for t in teams),
                parent_agent_id=None,
                is_self_built=None,
                created_at=record.created_at,
                updated_at=record.updated_at,
            ),
        )
    # 发布/审批状态批量附带（"我的智能体"页渲染状态标签用）：
    # 两步小查询——审批表一次 id IN + 市场名单一次全量（表小，内存过滤），
    # 不逐条查询。已在市场（含平台内置手动上架）恒为 approved。
    from bocomadp.market_review_store import STATUS_APPROVED, STATUS_REJECTED
    from bocomadp.market_review_store import review_status_map
    from bocomadp.market_store import list_market_entries

    status_map = await review_status_map(storage, owned_ids)
    market_map = {
        e.agent_id: e
        for e in await list_market_entries(storage)
        if e.agent_id in set(owned_ids)
    }
    for item in items:
        review = status_map.get(item.id)
        entry = market_map.get(item.id)
        if entry is not None:
            item.publish_status = STATUS_APPROVED
            _fill_publish_archive(
                item,
                entry.department,
                entry.system_name,
                entry.tag,
                entry.description,
            )
        elif review is None:
            continue  # 默认 not_submitted（schema 默认值）
        else:
            item.publish_status = review.status
            _fill_publish_archive(
                item,
                review.department,
                review.system_name,
                review.tag,
                review.description,
            )
        if review is not None:
            if review.status in (STATUS_APPROVED, STATUS_REJECTED):
                item.review_reason = review.reason
            item.reviewer = review.reviewer
            item.reviewed_at = review.reviewed_at
    total = len(items)
    start = (page_num - 1) * page_size
    return ListOwnedAgentsResponse(
        agents=items[start : start + page_size],
        total=total,
    )


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

    创建成功后**在 ``agents_extend`` 里预置一行**（``payload = {"skills": []}``）：
    新智能体必定没有已装技能，先落空记录可让 ``GET /workspace/skill`` 直接
    命中表、不必回源沙箱；该写入是尽力而为（失败只打 warning，读侧会回源
    并写回），不影响创建结果。

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
    parent_team = None
    if parent_id is not None:
        leader = await storage.get_agent(user_id, parent_id)
        if leader is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent agent '{parent_id}' not found.",
            )
        parent_team = await get_team(storage, user_id, parent_id)
        if parent_team is None:
            parent_team = ExpertTeamRelation(
                user_id=user_id,
                leader_agent_id=parent_id,
            )
        if len(parent_team.members) >= parent_team.max_members:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Team already at max_members={parent_team.max_members}."
                ),
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
            description=body.description,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
            invite_config=body.invite_config,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    record = AgentRecord(user_id=user_id, data=data)
    agent_id = await storage.upsert_agent(user_id, record)

    # 预置一行 agents_extend（payload.skills = []）：新智能体没有任何已装
    # 技能，先落一条空记录，让 GET /workspace/skill 直接命中表，不必解析
    # 会话 → 路由 Pod → exec 列举（Pod 不可用时那条路径会失败）。
    # **尽力而为**：写失败只打 warning —— 读侧找不到记录会自动回源沙箱并
    # 写回（等同于旧行为），不能为一条缓存把已建好的智能体变成 500。
    try:
        await ensure_agent_extend_row(storage, agent_id, source="agent-create")
    except Exception:  # noqa: BLE001 —— 缓存预置失败不影响创建主流程
        logger.warning(
            "create_agent: failed to preset agents_extend row for %s",
            agent_id,
            exc_info=True,
        )

    if body.is_team and parent_id is None:
        # Create an empty team "shell" so the agent is already classified
        # as an expert-team leader in listings (is_team=true) before any
        # member exists. Members can be added later via the team endpoints.
        await upsert_team(
            storage,
            ExpertTeamRelation(user_id=user_id, leader_agent_id=agent_id),
        )
    elif parent_team is not None:
        parent_team.add_member(agent_id, "self_built")
        await upsert_team(storage, parent_team)

    # 注意：创建接口不再自动写市场档案——市场名单完全由 agent_market
    # 表的行决定（有行 = 在市场）。平台内置智能体由运营手动 INSERT
    # 进名单；个人智能体由 owner 调 POST /agent/market/{id}/publish 上架。

    return CreateAgentResponse(agent_id=agent_id)


@agent_router.post(
    "/{agent_id}/copy",
    response_model=CopyAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Copy an agent (payload only)",
)
async def copy_agent(
    agent_id: str,
    body: CopyAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> CopyAgentResponse:
    """复制智能体**本体**（``AgentData``）。

    复制范围：

    - **复制**：``name`` / ``description`` / ``system_prompt`` /
      ``context_config`` / ``react_config`` / ``invite_config``
      （``invite_config`` **原样**复制——``invitable`` 与
      ``invite_description`` 都保留）；
    - **不复制**：已安装技能（技能按会话存在 workspace 里，不属于智能体
      配置）、工具/MCP 启停白名单、知识库配置、专家团（成员 / 团队档案 /
      handoff）、市场名单、记忆配置、沙箱并发配置、凭证绑定。

    响应里的 ``skills`` 取自**源**智能体在 ``agent_template`` 名单里的
    那一行（``agent_template.skills``，元素形如 ``namespace:name``），
    即"该模板期望安装的技能清单"：本端点只**返回清单**，不代为安装。
    源不在模板名单内（或存储层不可用）时为空列表——注意这**不是**权限
    判断，复制本身仍是"可读即可复制"。

    因此复制品的默认状态是：``is_team=False``、``parent_agent_id=None``、
    无技能、工具与 MCP 全部启用（新 id 在白名单里没有条目）、无知识库
    配置 / 无记忆、并发走默认值、模型凭证走运行时兜底解析。

    复制成功后会像 ``POST /agent/`` 一样**在 ``agents_extend`` 里预置一行**
    （``payload = {"skills": []}``）：复制品此刻确实没有已装技能，预置空行可
    让 ``/skills/*`` 查询与 ``GET /workspace/skill`` 不必回源沙箱；随后前端
    调 ``/skill/ensure`` 装的技能会逐条写穿到这一行。该写入是尽力而为（失败
    只打 warning，读侧会回源沙箱），不影响复制结果。

    权限：可读即可复制（:meth:`ResourceAccessService.resolve_agent`），
    不可见 → 404。命名：``body.name`` 缺省为 ``"<源名> 副本"``，允许与
    已有智能体重名。

    Args:
        agent_id (`str`):
            源智能体 id。
        body (`CopyAgentRequest`):
            复制参数（新名字）。
        user_id (`str`):
            Injected authenticated user ID（复制品归属该用户）。
        storage (`StorageBase`):
            Injected storage backend.
        access (`ResourceAccessService`):
            Injected resource access service（可见性校验 + 取源记录）。

    Returns:
        `CopyAgentResponse`:
            新智能体的完整视图——与 ``GET /agent/`` 列表元素、``PATCH``
            响应**同构**的 :class:`TeamAgentView`，可直接插进前端列表；
            额外带 ``agent_id``（与 ``id`` 同值）与 ``skills``（源模板行
            声明的技能清单）。

    Raises:
        `HTTPException`:
            404 if the source agent is not visible to the caller.
    """
    src = await access.resolve_agent(user_id, agent_id)

    # 两个 id 必须同时更换：``agents.id``（行主键）与 ``payload.data.id``
    # 在框架里是各自独立生成的（存量行实测即不相等），只换主键会在 payload
    # 里残留**源**的 data.id。
    new_id = _generate_id()
    payload = {**src.data.model_dump(exclude={"id"}), "id": new_id}
    new_name = (body.name or "").strip()
    payload["name"] = new_name or f"{src.data.name} 副本"

    await storage.upsert_agent(
        user_id,
        AgentRecord(id=new_id, user_id=user_id, data=AgentData(**payload)),
    )

    # 预置一行 agents_extend（payload.skills = []），与 POST /agent/ 完全一致：
    # 复制品当前**没有任何已装技能**（响应里的 ``skills`` 只是源模板声明的
    # 清单，由前端随后调 /skill/ensure 安装；装成后 ensure 会逐条写穿本表，
    # 见 skill_router._record_installed_skill）。不预置的话，复制品属于
    # "无记录"，两个 /skills/* 查询每次都要回源沙箱（申请 Pod）。
    # 尽力而为：写失败只打 warning —— 读侧找不到记录时仍会回源沙箱。
    try:
        await ensure_agent_extend_row(storage, new_id, source="agent-copy")
    except Exception:  # noqa: BLE001 —— 缓存预置失败不影响复制主流程
        logger.warning(
            "copy_agent: failed to preset agents_extend row for %s",
            new_id,
            exc_info=True,
        )

    logger.info(
        "copy_agent: %s → %s (by=%s, name=%r)",
        agent_id,
        new_id,
        user_id,
        payload["name"],
    )

    # 响应 = 新智能体的完整视图（与 GET /agent/ 列表元素、PATCH 响应同构），
    # 前端可直接把它当成一个智能体对象插进列表，省一次 GET。
    # ``_to_team_view`` 就是 PATCH 用的那个构造函数（复制品归属调用者，
    # 所以 editable 恒 True；团队关系不复制 → is_team=False）。
    stored = await storage.get_agent(user_id, new_id)
    if stored is None:  # 理论不可达：刚 upsert 的行读不到
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Copied agent was not persisted.",
        )
    view = await _to_team_view(storage, user_id, stored)

    # 模板声明的技能清单：``agent_template`` 里**源**智能体那一行的
    # ``skills``（"该模板期望安装的技能"，元素 namespace:name）。只透出
    # 清单、不代为安装；不在名单内 / 存储层不可用 → 空列表。
    template = await get_template_entry(storage, agent_id)
    template_skills = list(template.skills) if template is not None else []

    return CopyAgentResponse(
        **view.model_dump(),
        agent_id=new_id,
        skills=template_skills,
    )


@agent_router.patch(
    "/{agent_id}",
    response_model=TeamAgentView,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> TeamAgentView:
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
    # leader must stay invitable no matter what the frontend sends.
    # Without this, a PATCH that flips ``invite_config.invitable`` off
    # (or strips the description) would silently orphan the member from
    # the leader's ``AgentInvite`` pool and break the assembled workflow
    # chain later. Membership now lives in the ``expert_team_relations``
    # table, so the member's leader is looked up there.
    member_leader_id = None
    for team in await list_teams(storage, owner_id):
        if team.is_self_built(existing.id):
            member_leader_id = team.leader_agent_id
            break
    if member_leader_id is not None:
        inv = updates.get("invite_config") or (
            existing.data.invite_config or InviteConfig()
        ).model_dump()
        inv = {**inv, "invitable": True}
        if not (inv.get("invite_description") or "").strip():
            leader = await storage.get_agent(
                owner_id,
                member_leader_id,
            )
            leader_name = (
                leader.data.name
                if leader is not None
                else member_leader_id
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
    return await _to_team_view(storage, owner_id, updated_agent)


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
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Permanently delete an agent configuration.

    Cascades through every session owned by this agent (and, for team
    leaders, through every worker session) — cancelling any in-flight
    chat run, removing storage records, and purging bus state.

    删除成功后**级联清理该智能体的市场档案**（``agent_market`` 行）。
    不清理会留下孤儿档案（指向已删智能体的死数据）。团队成员级联删除
    等绕过本接口的路径，由启动时 ``prune_orphan_market_entries`` 兜底。

    同时**级联清理扩展表记录**（``agents_extend`` 行，见
    :func:`bocomadp.agents_extend_store.delete_agent_extend`），与创建接口
    预置空记录成对，不留孤儿行；该清理失败只打 warning，不影响删除结果。

    Args:
        agent_id (`str`): The agent to delete.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.
        access (`ResourceAccessService`): Injected access service — used
            to resolve the owning user and enforce the edit permission
            when a shared editor deletes the agent.
        storage (`StorageBase`): Injected storage — used to cascade the
            agent-market entry and the ``agents_extend`` row.

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
    # 市场档案级联清理 + 控制台留痕（失败不影响删除主流程）
    from bocomadp.market_audit import log_audit
    from bocomadp.market_store import delete_market_entry

    if await delete_market_entry(storage, agent_id):
        log_audit(
            user_id,
            "delete_agent_market",
            target=agent_id,
            detail="删除智能体级联清理市场档案",
        )
    # 发布审批记录级联清理（防幽灵待办条目）
    from bocomadp.market_review_store import delete_review_record

    if await delete_review_record(storage, agent_id):
        log_audit(
            user_id,
            "delete_agent_review",
            target=agent_id,
            detail="删除智能体级联清理发布审批记录",
        )
    # 扩展表（agents_extend）级联清理：不留指向已删智能体的孤儿行
    # （读侧只在活智能体上查询，孤儿行无害但会一直占着；同步清理比
    # 依赖启动扫描更及时）。失败同样不影响删除主流程。
    try:
        await delete_agent_extend(storage, agent_id)
    except Exception:  # noqa: BLE001 —— 缓存清理失败不影响删除结果
        logger.warning(
            "delete_agent: failed to drop agents_extend row for %s",
            agent_id,
            exc_info=True,
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
            "Always True under scheme B: every team member is created under "
            "this leader (parent_agent_id == leader) and belongs exclusively "
            "to this team. The historical 'invited by reference' member type "
            "is no longer supported, so this flag is now constant."
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

    Only ``collaboration_mode``, ``handoff_relations`` and ``max_members``
    are replaced. Team membership is **not** mutated by this endpoint:
    members are created exclusively through ``POST /agent/`` with
    ``parent_agent_id`` (self-built agents that belong only to this team)
    and removed via
    ``DELETE /agent/{agent_id}/team/members/{member_id}``. External /
    invited agents are no longer supported, so there is no ``member_ids``
    field here.
    """

    collaboration_mode: Literal["free_handoff", "workflow"] = "free_handoff"
    handoff_relations: list[HandoffRelation] = Field(default_factory=list)
    max_members: int = 10


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


async def _to_team_view(
    storage: StorageBase,
    owner_id: str,
    record: AgentRecord,
) -> TeamAgentView:
    """Build a :class:`TeamAgentView` from an agent record.

    ``is_team`` / ``parent_agent_id`` / ``is_self_built`` were removed
    from the framework :class:`AgentView`; they are re-derived here from
    the ``expert_team_relations`` table so the wire contract is
    unchanged.
    """
    view = TeamAgentView.model_validate(
        {**record.model_dump(), "editable": True},
    )
    for team in await list_teams(storage, owner_id):
        if team.leader_agent_id == record.id:
            view.is_team = True
        if team.is_self_built(record.id):
            view.parent_agent_id = team.leader_agent_id
            view.is_self_built = True
    # 发布/审批状态附带（单条，与列表接口同口径）
    await _attach_publish_status(storage, [view])
    return view


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

    Lists each member (all self-built, exclusive to this team) with
    denormalized name and description. Raises 404 if the agent is not
    visible to the caller. The agent need not yet be a team (empty
    member_ids is reported).
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    rel = await get_team(storage, owner_id, agent_id)
    cfg = rel or ExpertTeamRelation(
        user_id=owner_id,
        leader_agent_id=agent_id,
    )
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
                is_self_built=cfg.is_self_built(m.id),
            )
        )
    return TeamConfigResponse(
        agent_id=agent.id,
        name=agent.data.name,
        # "is_team" here is the same semantic as in ``AgentView``:
        # a row in ``expert_team_relations`` marks the agent as a team
        # leader (so an empty shell is still classified as a team).
        is_team=rel is not None,
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
    """Replace the leader's team configuration.

    Only ``collaboration_mode``, ``handoff_relations`` and ``max_members``
    are replaced. Membership is **not** mutated here: team members are
    created exclusively through ``POST /agent/`` with ``parent_agent_id``
    (self-built agents that belong only to this team) and removed via
    ``DELETE /agent/{agent_id}/team/members/{member_id}``. External /
    invited agents are no longer supported, so this endpoint no longer
    accepts a ``member_ids`` field.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    rel = await get_team(storage, owner_id, agent_id)
    if rel is None:
        rel = ExpertTeamRelation(user_id=owner_id, leader_agent_id=agent_id)
    rel.collaboration_mode = body.collaboration_mode
    rel.max_members = body.max_members
    rel.handoff_relations = list(body.handoff_relations)
    await upsert_team(storage, rel)
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
    storage: StorageBase = Depends(get_storage),
) -> CollaborationModeResponse:
    """Return the current collaboration mode — ``free_handoff`` (soft
    guidance) or ``workflow`` (hard ordering). Defaults to
    ``free_handoff`` for agents without a team row yet.
    """
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    rel = await get_team(storage, owner_id, agent_id)
    return CollaborationModeResponse(
        collaboration_mode=(
            rel.collaboration_mode if rel is not None else "free_handoff"
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
    rel = await get_team(storage, owner_id, agent_id)
    if rel is None:
        rel = ExpertTeamRelation(user_id=owner_id, leader_agent_id=agent_id)
    rel.collaboration_mode = body.collaboration_mode
    await upsert_team(storage, rel)
    return CollaborationModeResponse(collaboration_mode=rel.collaboration_mode)


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
    """Remove ``member_id`` from the team.

    Under scheme B every team member is self-built (``parent_agent_id ==
    leader``) and belongs exclusively to this team, so removing it
    cascade-deletes the underlying agent (sessions + agent index
    included). The removed member is also detached from any
    handoff_relations on this team.

    Borrowed-session cleanup
        A self-built member's team conversation lives in a
        ``team:<leader_team_id>/invited:<handle>``-named borrowed
        session (the runtime ``AgentInvite`` pool, unrelated to the
        removed config-layer invite) whose ``team_id`` references this
        team's roster. If we leave that session behind, two things
        break later:

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
    rel = await get_team(storage, owner_id, agent_id)
    if rel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent is not a team leader.",
        )
    if member_id not in rel.member_ids:
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

    rel.remove_member(member_id)
    rel.handoff_relations = [
        r
        for r in rel.handoff_relations
        if r.from_agent_id != member_id and r.to_agent_id != member_id
    ]

    # A team member is always self-built and belongs exclusively to this
    # team, so removing it drops the underlying agent (cascading its
    # sessions and agent index entry). There is no "invited" member type
    # to merely unlink under scheme B.
    await session_service.delete_agent(owner_id, member_id)
    await upsert_team(storage, rel)
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
    storage: StorageBase = Depends(get_storage),
) -> HandoffRelationResponse:
    """Return the leader's handoff relations (collaboration order)."""
    owner_id, agent = await access.resolve_for_edit(
        user_id,
        ResourceKind.AGENT,
        agent_id,
    )
    _require_leader(agent)
    rel = await get_team(storage, owner_id, agent_id)
    if rel is None:
        return HandoffRelationResponse(handoff_relations=[])
    return HandoffRelationResponse(handoff_relations=rel.handoff_relations)


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
    rel = await get_team(storage, owner_id, agent_id)
    if rel is None:
        rel = ExpertTeamRelation(user_id=owner_id, leader_agent_id=agent_id)
    allowed = {agent_id, *rel.member_ids}
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
    rel.handoff_relations = list(body.handoff_relations)
    await upsert_team(storage, rel)
    return HandoffRelationResponse(handoff_relations=rel.handoff_relations)
