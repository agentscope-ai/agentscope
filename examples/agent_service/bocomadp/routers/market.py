# -*- coding: utf-8 -*-
"""BocomADP agent-market router — 市场查询 + 精选推荐 + 上架管理 + 标签管理。

市场名单完全由 ``agent_market`` 表的行决定（**有行 = 在市场**）：
``agents`` 表是全量智能体，``user_id`` 不再承载"平台"语义。名单来源：

- 平台内置智能体：运营手动 INSERT（``agent_id`` 填 ``agents.id``）；
- 个人智能体：owner 调 publish 插行、unpublish 删行。

标签即 ``agent_market.tag`` 自由字符串（≤64 字符）：打标内容原样存储，
无预设清单；空串 = 未打标。打标/撕标**仅限名单内智能体**（不在名单
404，打标不允许隐式上架），权限全开放（带 X-User-ID 即可调）。

端点（统一挂 ``/agent/market`` 前缀，main.py 再统一加 ``/api``）：

查询类（无权限要求）：
- ``GET  /agent/market``              市场列表（名单内全部；按
                                       updated_at 倒序分页，可按 tag 筛选）；
- ``GET  /agent/market/featured``     精选推荐：按实时热度（sessions 会话数）
                                       倒序取前 N，同分按 updated_at 倒序兜底。

上架/审批（**发布走审批流**，approve 后才插名单行，市场查询类接口零变化）：
- ``POST /agent/market/{agent_id}/publish``        发布：写审批表 pending（幂等）；
- ``POST /agent/market/{agent_id}/unpublish``      撤回：删市场行 + 清审批记录；
- ``GET  /agent/market/reviews``                   审批列表（默认待办，任何登录用户）；
- ``POST /agent/market/reviews/{id}/approve``      通过：插名单行上架（任何登录用户）；
- ``POST /agent/market/reviews/{id}/reject``       拒绝：带理由（任何登录用户）。

审批三件套不校验审批人名单，任何登录用户都可审批；白名单管理接口
保留、行为不变。

审批人白名单（JSON 文件持久化 + 接口管理 + 末位保护；写操作仅
名单内用户）：
- ``GET/POST/PUT /agent/market/reviewers``、
  ``DELETE /agent/market/reviewers/{target_user_id}``；
- 生效名单 = JSON 文件内容（无写死账号，纯数据驱动）；末位保护：
  PUT 传空 / 删最后一个审批人 → 409"至少保留一个审批人"；详见
  :mod:`bocomadp.market_reviewers`。

标签管理（全开放，仅名单内智能体）：
- ``PUT  /agent/market/{agent_id}``   打标（tag 非空）或撕标（tag 空串）；
- ``DELETE /agent/market/{agent_id}`` 撕标：tag 置空（204，幂等）。

设计要点：

- **查询实现**：两步小查询——先查 ``agent_market`` 拿全部名单 id，
  再查 ``agents`` 表取本体（``id IN 名单``，取 name/source/时间戳），
  风格与 ``_tag_map`` / 热度聚合的分步小查询一致。
- **不做查询过滤**：名单完全由运营手动维护 + owner 发布构成，
  放进去什么就展示什么（系统内置 ``_`` 开头智能体是代码硬编码创建
  的内部工具，运营手动维护名单时不放进即可）。
- **热度（实时聚合）**：heat 不落库，每次查询时对 ``sessions``
  表按 ``agent_id`` GROUP BY 计数。**按市场智能体集合过滤，而不是按
  会话的 user_id 过滤**——sessions.user_id 记的是使用者，任何人对
  市场内智能体的使用都计入该智能体热度。将来量大再升级为固化分值
  + 定时增量累加，接口签名不变。
- **操作留痕只打控制台日志**：上架/标签操作调用
  :func:`bocomadp.market_audit.log_audit`，以 ``[AGENT_MARKET_AUDIT]``
  前缀打印 INFO，用 ``docker compose logs -f agentscope-service``
  直接查看，不再落库（将来要长期留痕只需替换该模块的日志实现）。
- 框架的 ``AgentRow`` / ``SessionRow`` 是私有模块，但 bocomadp 层
  访问框架私有成员（``_session_factory``）已有 ``team_store.py`` 先例，
  此处一致：只读不写框架表。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

logger = logging.getLogger("bocomadp.market_router")

from agentscope.app.deps import get_current_user_id, get_storage
from agentscope.app.storage import StorageBase
from agentscope.app.storage._sql._tables import AgentRow, SessionRow

from bocomadp.market_audit import log_audit
from bocomadp.market_review_store import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    count_reviews_by_status,
    decide_review,
    delete_review_record,
    get_review_record,
    list_reviews,
    list_reviews_by_status,
    upsert_pending_review,
)
from bocomadp.market_reviewers import effective_reviewers, is_reviewer
from bocomadp.market_store import (
    AgentMarketEntry,
    delete_market_entry,
    get_market_entry,
    insert_market_entry,
    list_market_entries,
    list_market_tags,
    set_market_tag,
)
from bocomadp.routers._schema.market import (
    MarketAgentView,
    MarketApproveRequest,
    MarketEntryView,
    MarketListResponse,
    MarketPublishRequest,
    MarketPublishStatusView,
    MarketRejectRequest,
    MarketReviewListResponse,
    MarketReviewerView,
    MarketReviewersResponse,
    MarketReviewersUpdateRequest,
    MarketReviewItemView,
    MarketUpsertRequest,
)

market_router = APIRouter(
    prefix="/agent/market",
    tags=["agent-market"],
    responses={404: {"description": "Not found"}},
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

# tag 列宽（与 market_store.AgentMarketRow.tag 一致）
_TAG_MAX_LEN = 64


async def _find_agent_row(
    storage: StorageBase,
    agent_id: str,
) -> AgentRow | None:
    """按 id 查智能体（不限定归属者）——打标/上架的存在性校验用。"""
    factory = getattr(storage, "_session_factory", None)
    if factory is None:
        return None
    async with factory() as session:
        return (
            await session.execute(
                select(AgentRow).where(AgentRow.id == agent_id),
            )
        ).scalars().first()


async def _market_agent_rows(storage: StorageBase) -> list[AgentRow]:
    """市场可见的 agents 行（updated_at 倒序），范围 = agent_market 名单。

    两步小查询：先拿 ``agent_market`` 全部名单 id，再按 ``id IN 名单``
    查 agents 本体。**不做防御性过滤**——名单完全由运营手动维护 +
    owner 发布构成，放进去什么就展示什么。
    返回完整 ORM 行（名称在 ``payload["data"]["name"]``，
    框架 mapper 的存储契约：payload = record.model_dump 去掉
    id/created_at/updated_at/user_id/source 后的剩余部分，即
    ``{"data": {...AgentData...}}``）。
    """
    factory = getattr(storage, "_session_factory", None)
    if factory is None:
        return []
    market_ids = {
        e.agent_id for e in await list_market_entries(storage)
    }
    if not market_ids:
        return []
    async with factory() as session:
        rows = (
            await session.execute(
                select(AgentRow)
                .where(AgentRow.id.in_(market_ids))
                .order_by(
                    AgentRow.updated_at.desc(),
                    AgentRow.id.desc(),  # 同 updated_at 时保证顺序稳定
                ),
            )
        ).scalars().all()
    return list(rows)


async def _session_heat(
    storage: StorageBase,
    market_agent_ids: set[str],
) -> dict[str, int]:
    """实时热度：sessions 表按 ``agent_id`` 聚合的会话数。

    **按市场智能体集合过滤，而不是按会话的 user_id 过滤**：
    sessions.user_id 记的是**使用者**，不是
    智能体拥有者。若按拥有者筛，普通用户（如 lrm）使用市场智能体
    产生的会话就会被漏掉，热度永远只统计运营自己的量。改为
    ``agent_id ∈ 市场智能体集合`` 后，任何人对市场内智能体的使用
    都计入该智能体热度。
    """
    if not market_agent_ids:
        return {}
    factory = getattr(storage, "_session_factory", None)
    if factory is None:
        return {}
    async with factory() as session:
        counts = (
            await session.execute(
                select(SessionRow.agent_id, func.count(SessionRow.id))
                .where(SessionRow.agent_id.in_(market_agent_ids))
                .group_by(SessionRow.agent_id),
            )
        ).all()
    return {agent_id: int(count) for agent_id, count in counts}


async def _entry_map(storage: StorageBase) -> dict[str, AgentMarketEntry]:
    """{agent_id: AgentMarketEntry} 市场名单行映射（tag + 发布档案）。

    发布档案（部门/系统/业务条线/说明）approve 上架时随行落库，
    市场列表/精选从这里取（审批表里的值不回读——上架即定格）。
    """
    return {e.agent_id: e for e in await list_market_entries(storage)}


def _row_to_view(
    row: AgentRow,
    entry: AgentMarketEntry | None,
    heat: int,
) -> MarketAgentView:
    # payload 存储契约见 _market_agent_rows docstring：名称/简介/提示词嵌在
    # payload["data"] 下（AgentData.name / description / system_prompt）；
    # get("data", {}) 兜底防御历史脏数据。
    #
    # ``description`` 取**智能体自身**的简介
    # （``agents.payload.data.description``，创建/编辑时作者填的那份）；
    # **不取**发布档案里的 ``agent_market.description``——那是审批弹窗的
    # 用途说明，市场卡片要展示的是智能体作者的介绍。
    # ``department`` / ``system_name`` 智能体侧没有对应字段，仍取发布档案；
    # ``tag``（业务条线）同理。
    data = row.payload.get("data", {}) if isinstance(row.payload, dict) else {}
    e = entry or AgentMarketEntry(agent_id=row.id)
    return MarketAgentView(
        id=row.id,
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        system_prompt=str(data.get("system_prompt", "")),
        source=row.source,
        tag=e.tag,  # 名单内必有行；空串 = 未打标（业务条线随发布带入）
        department=e.department,
        system_name=e.system_name,
        heat=heat,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _require_market_agent(
    storage: StorageBase,
    agent_id: str,
) -> AgentRow:
    """打标/撕标公共前置校验，失败直接抛 HTTPException。

    - 智能体不存在 → 404；
    - 不在市场名单内（``agent_market`` 无行）→ 404：打标不允许
      隐式上架，上架只能走运营手动 INSERT 或 publish 接口。
    """
    record = await _find_agent_row(storage, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"智能体不存在: {agent_id}",
        )
    if await get_market_entry(storage, agent_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="该智能体不在市场名单内（agent_market 无记录），不能打标。",
        )
    return record


# ---------------------------------------------------------------------------
# 查询端点
# ---------------------------------------------------------------------------


@market_router.get(
    # 注意：路径必须是空串（prefix 已含 /agent/market）。若写 "/"，
    # 注册路径会变成 "/agent/market/"（尾巴斜杠），请求 /agent/market
    # 时无完全匹配，会被 agent_router 的 PATCH /agent/{agent_id} 部分
    # 匹配吞掉返回 405（/featured 等精确路径不受影响，极隐蔽）。
    "",
    response_model=MarketListResponse,
    summary="平台市场智能体列表",
)
async def list_market_agents(
    tag: str | None = Query(
        default=None,
        description=(
            "按标签筛选。传了该参数时，未打标的智能体不会出现在结果里；"
            "不传则全部返回（tag='' 表示未打标）。"
        ),
    ),
    page_num: int = Query(
        default=1,
        ge=1,
        alias="pageNum",
        description="Page number, 1-based.",
    ),
    page_size: int = Query(
        default=10,
        ge=1,
        le=100,
        alias="pageSize",
        description="Page size (items per page), 1-100.",
    ),
    storage: StorageBase = Depends(get_storage),
) -> MarketListResponse:
    """市场列表：``agent_market`` 名单内全部智能体。

    默认按 ``updated_at`` 倒序（最近更新/上架在前）——热度排序是精选
    推荐接口的专职，本接口不掺和。附带实时热度与标签供前端展示。
    """
    rows = await _market_agent_rows(storage)
    entries = await _entry_map(storage)
    heats = await _session_heat(storage, {r.id for r in rows})

    items: list[MarketAgentView] = []
    for row in rows:
        t = entries[row.id].tag if row.id in entries else ""
        if tag is not None and t != tag:
            continue
        items.append(
            _row_to_view(row, entries.get(row.id), heats.get(row.id, 0)),
        )

    total = len(items)
    start = (page_num - 1) * page_size
    return MarketListResponse(
        agents=items[start : start + page_size],
        total=total,
    )


@market_router.get(
    "/featured",
    response_model=MarketListResponse,
    summary="精选推荐：实时热度倒序取前 N",
)
async def featured_market_agents(
    top: int = Query(
        default=4,
        ge=1,
        le=50,
        description="取前 N 个，默认 4（可传 3）。",
    ),
    storage: StorageBase = Depends(get_storage),
) -> MarketListResponse:
    """精选推荐：按实时热度（sessions 会话数）倒序取前 N。

    范围同市场列表（``agent_market`` 名单内全部）。
    - 热度是**累计使用量**：刚发布的智能体热度为 0，属正常状态，
      不做 ``>0`` 之类的过滤；
    - 同分的（如上线初期大家都是 0 分）按 ``updated_at`` 倒序兜底，
      保证顺序稳定、页面不空窗；
    - 取不满 N 个就返回实际条数。
    """
    rows = await _market_agent_rows(storage)
    entries = await _entry_map(storage)
    heats = await _session_heat(storage, {r.id for r in rows})

    ranked = sorted(
        rows,
        key=lambda r: (heats.get(r.id, 0), r.updated_at),
        reverse=True,
    )[:top]

    return MarketListResponse(
        agents=[
            _row_to_view(r, entries.get(r.id), heats.get(r.id, 0))
            for r in ranked
        ],
        total=len(ranked),
    )


# ---------------------------------------------------------------------------
# 标签清单端点（全开放）：前端筛选下拉框数据源
# ---------------------------------------------------------------------------


@market_router.get(
    "/tags",
    summary="市场已使用的标签清单（去重，升序）",
)
async def list_market_tag_options(
    storage: StorageBase = Depends(get_storage),
) -> dict:
    """全量标签：``agent_market.tag`` 现存量去重（空串不进清单）。

    自由标签口径下不存在预设清单（旧版 ``config.yaml`` 的
    ``domains`` 已废），这是前端标签下拉框的唯一真实数据源。
    """
    return {"tags": await list_market_tags(storage)}


# ---------------------------------------------------------------------------
# 审批人白名单管理（GET 公开；写操作仅名单内用户）
# 注意：必须注册在 PUT /agent/market/{agent_id}（打标）之前——
# PUT /agent/market/reviewers 与 PUT /agent/market/{agent_id} 同为
# 两段路径，注册顺序决定匹配优先级。
# ---------------------------------------------------------------------------


def _require_whitelist_manager(user_id: str) -> None:
    """白名单写操作公共前置：不在生效名单内 → 403。"""
    if not is_reviewer(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有审批人白名单内的用户可以修改审批人名单。",
        )


def _reviewers_response() -> MarketReviewersResponse:
    return MarketReviewersResponse(
        reviewers=[
            MarketReviewerView(user_id=uid) for uid in effective_reviewers()
        ],
    )


@market_router.get(
    "/reviewers",
    response_model=MarketReviewersResponse,
    summary="查询审批人白名单（公开）",
)
async def list_market_reviewers(
    user_id: str = Depends(get_current_user_id),
) -> MarketReviewersResponse:
    """生效名单 = JSON 文件内容（排序返回）。

    首次部署文件为空 → 返回空名单（等待运维手工种入第一批审批人）。
    """
    return _reviewers_response()


@market_router.post(
    "/reviewers",
    response_model=MarketReviewersResponse,
    summary="新增审批人（批量、幂等；仅名单内用户）",
)
async def add_market_reviewers(
    body: MarketReviewersUpdateRequest,
    user_id: str = Depends(get_current_user_id),
) -> MarketReviewersResponse:
    """批量新增审批人：空白项忽略、已存在的跳过（幂等，不报错）。

    写盘原子化，改完立即生效。
    """
    _require_whitelist_manager(user_id)
    from bocomadp.market_reviewers import add_reviewers

    added = add_reviewers(body.user_ids)
    log_audit(
        user_id,
        "add_market_reviewers",
        detail=f"新增 {added} 人：{[u for u in body.user_ids if u.strip()]}",
    )
    return _reviewers_response()


@market_router.put(
    "/reviewers",
    response_model=MarketReviewersResponse,
    summary="全量覆盖审批人名单（仅名单内用户；不可清空）",
)
async def overwrite_market_reviewers(
    body: MarketReviewersUpdateRequest,
    user_id: str = Depends(get_current_user_id),
) -> MarketReviewersResponse:
    """全量覆盖名单（"清空重来"的显式出口）。

    末位保护：清单为空 / 全空白 → 409"至少保留一个审批人"——无锚点
    模型下这是唯一的防自锁防线。
    """
    _require_whitelist_manager(user_id)
    from bocomadp.market_reviewers import overwrite_reviewers

    result = overwrite_reviewers(body.user_ids)
    if result == "empty":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="至少保留一个审批人，不能清空白名单。",
        )
    log_audit(
        user_id,
        "overwrite_market_reviewers",
        detail=f"全量覆盖，现有 {len(effective_reviewers())} 人",
    )
    return _reviewers_response()


@market_router.delete(
    "/reviewers/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除审批人（仅名单内用户；末位保护）",
)
async def remove_market_reviewer(
    target_user_id: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """删单个审批人：

    - 是最后一个审批人 → 409（至少保留一个审批人，防自锁）；
    - 不在名单（从未加过 / 已删）→ 404；
    - 成功 → 204，立即生效。
    """
    _require_whitelist_manager(user_id)
    from bocomadp.market_reviewers import remove_reviewer

    result = remove_reviewer(target_user_id)
    if result == "last_one":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="至少保留一个审批人，不能删除最后一个审批人。",
        )
    if result == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"该用户不在审批人名单中: {target_user_id}",
        )
    log_audit(
        user_id,
        "remove_market_reviewer",
        target=target_user_id,
        detail="移出审批人白名单",
    )


# ---------------------------------------------------------------------------
# 标签管理端点（全开放，无权限门槛；仅市场名单内智能体）
# ---------------------------------------------------------------------------


@market_router.put(
    "/{agent_id}",
    response_model=MarketEntryView,
    summary="设置智能体标签（全开放，仅市场名单内）",
)
async def upsert_market_agent(
    agent_id: str,
    body: MarketUpsertRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketEntryView:
    """给市场名单内的智能体打标 / 撕标。

    - ``tag`` 是自由字符串（≤64 字符），原样存储，**不做清单校验**；
    - 传空串 = **撕标**（``tag`` 置空，回到"未打标"状态），
      与 ``DELETE /agent/market/{agent_id}`` 等效；
    - 智能体必须在市场名单内（``agent_market`` 有行），否则 404——
      打标不允许隐式上架；上架走运营手动 INSERT 或 publish 接口；
    - 智能体本体不存在 404；
    - 热度不在本接口的管理范围内——热度是实时聚合值，无可配置项。
    """
    tag = (body.tag or "").strip()
    if len(tag) > _TAG_MAX_LEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"标签最长 {_TAG_MAX_LEN} 字符。",
        )
    await _require_market_agent(storage, agent_id)

    await set_market_tag(storage, agent_id, tag)
    saved = await get_market_entry(storage, agent_id)
    assert saved is not None  # 前置校验已确认在名单内
    log_audit(
        user_id,
        "set_agent_tag" if tag else "clear_agent_tag",
        target=agent_id,
        detail=f"tag={tag}" if tag else "撕标（tag 置空）",
    )
    return MarketEntryView(**saved.model_dump())


@market_router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="撕标：清空标签（全开放，仅市场名单内）",
)
async def delete_market_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """撕标：把标签真正撕掉（``tag`` 置空 = 未打标）。

    纯标签操作，**不影响市场名单本身**（把智能体移出市场走
    ``POST /agent/market/{agent_id}/unpublish``）。智能体本体不动；
    幂等：重复调用效果一致。不在市场名单内 404。
    """
    await _require_market_agent(storage, agent_id)
    await set_market_tag(storage, agent_id, "")
    log_audit(
        user_id,
        "clear_agent_tag",
        target=agent_id,
        detail="撕标（tag 置空）",
    )


# ---------------------------------------------------------------------------
# 上架管理（仅智能体 owner）
# ---------------------------------------------------------------------------


def _publish_guard(
    agent_id: str,
    record: AgentRow,
    user_id: str,
) -> None:
    """上架/撤回的公共前置校验，失败直接抛 HTTPException。

    - 非 owner（X-User-ID ≠ agents.user_id）→ 403。
    """
    if record.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有智能体拥有者可以发布/撤回发布。",
        )


@market_router.post(
    "/{agent_id}/publish",
    response_model=MarketPublishStatusView,
    summary="发布智能体到市场（仅 owner，进入审批）",
)
async def publish_market_agent(
    agent_id: str,
    body: MarketPublishRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketPublishStatusView:
    """发布 = 提交审批申请（写 ``agent_market_review``，status=pending）。

    请求体是发布弹窗表单：部门 / 系统 / 业务条线（tag）/ 说明四项
    **全必填**（tag 空串或纯空白 → 422）——随审批记录落库，审批人
    据此判断批不批；approve 上架时复制进 ``agent_market`` 行供市场展示。

    **不再直接上架**——审批人通过（approve）后才插 ``agent_market``
    名单行。幂等口径：

    - 已在市场（审批通过上架 / 平台内置手动上架）→ 幂等返回 approved，
      不重复审批（上架后内容锁定，无"变更重审"概念）；
    - 已 pending → 幂等返回当前申请（前端连点不报错、不建重复记录），
      **表单字段照常覆盖**（改完弹窗再点发布，存的信息要最新）；
    - rejected → 重置回 pending 并清空旧结论（reason/reviewer 作废）；
    - 首次 → 新建 pending 记录。

    其他：仅 owner（403）、智能体不存在（404）。
    """
    record = await _find_agent_row(storage, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"智能体不存在: {agent_id}",
        )
    _publish_guard(agent_id, record, user_id)

    existing = await get_market_entry(storage, agent_id)
    if existing is not None:
        log_audit(
            user_id,
            "publish_agent_market",
            target=agent_id,
            detail="重复发布（已在市场，幂等返回 approved）",
        )
        return MarketPublishStatusView(
            agent_id=agent_id,
            status=STATUS_APPROVED,
            applicant=record.user_id,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    review = await upsert_pending_review(
        storage,
        agent_id,
        user_id,
        meta={
            "department": body.department.strip(),
            "system_name": body.system_name.strip(),
            "tag": (body.tag or "").strip(),
            "description": body.description.strip(),
        },
    )
    log_audit(
        user_id,
        "publish_agent_market",
        target=agent_id,
        detail="提交发布申请（进入审批 pending）",
    )
    return MarketPublishStatusView(**review.model_dump())


@market_router.post(
    "/{agent_id}/unpublish",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="下架/撤回（**人人可操作**，设计阶段市场操作全放开）",
)
async def unpublish_market_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """下架 = 移出市场（若有）+ 清审批记录（若有），一条接口全覆盖：

    - pending：撤回申请（删审批行，回到"未提交"）；
    - approved：从市场下架（删 ``agent_market`` 行，**标签随行消失**，
      重新发布需重走审批、重打标签）——创建者的 ``/agent/owned``
      列表里状态自动回落为 ``not_submitted``（待发布）；
    - rejected：清掉拒绝记录（改完直接重新 publish）；
    - 幂等：什么都没有也 204。

    **权限全放开**：智能体市场的任何操作人人都有权限——任何登录用户
    （带 X-User-ID）都可下架/撤回，包括非 owner 撤别人的待审申请；
    操作人记入审计日志留痕。智能体不存在 404。
    """
    record = await _find_agent_row(storage, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"智能体不存在: {agent_id}",
        )

    await delete_review_record(storage, agent_id)
    await delete_market_entry(storage, agent_id)
    log_audit(
        user_id,
        "unpublish_agent_market",
        target=agent_id,
        detail=(
            f"下架/撤回发布（删市场行 + 清审批记录），"
            f"owner={record.user_id}"
        ),
    )


# ---------------------------------------------------------------------------
# 发布审批（审批列表 + 通过/拒绝，操作侧不校验审批人白名单）
# 单查发布状态不再单独提供：owner 视角走 GET /agent/owned 与
# GET /agent/（publish_status / publish_info / review_reason / reviewer /
# reviewed_at），审核人视角走 GET /agent/market/reviews/{agent_id}。
# ---------------------------------------------------------------------------


def _require_reviewer(user_id: str) -> None:
    """审批侧公共前置：只要求带 X-User-ID 的登录用户，不校验审批人
    白名单。

    要启用审批人白名单时，把这里换成 ``is_reviewer(user_id)`` 校验
    （不在名单内抛 403）即可，调用方无需改动。
    """
    return


@market_router.get(
    "/reviews",
    response_model=MarketReviewListResponse,
    summary="审核列表（status 不传 = 全部；keyword 模糊搜名称/提交人）",
)
async def list_market_reviews(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description=(
            "审批状态筛选：pending / approved / rejected / all；"
            "**不传 = 全部**（待审核+已通过+已驳回的 所有记录）。"
        ),
    ),
    keyword: str | None = Query(
        default=None,
        description=(
            "模糊搜索（单字段双列 OR）：智能体名称 **或** 提交人 "
            "（applicant user_id）任一包含即命中，大小写不敏感。"
        ),
    ),
    page_num: int = Query(
        default=1,
        ge=1,
        alias="pageNum",
        description="Page number, 1-based.",
    ),
    page_size: int = Query(
        default=10,
        ge=1,
        le=100,
        alias="pageSize",
        description="Page size (items per page), 1-100.",
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketReviewListResponse:
    """审核工作台条件查询：

    - ``status``：不传/传 all = 全部三种状态（按**提交时间倒序**，
      最近的申请在前）；pending = 待办（先到先审，申请时间正序）；
      approved/rejected = 已办（按审批时间倒序，最近处理在前）。
    - ``keyword``：名称/提交人二选一模糊匹配（SQL 语义
      ``name LIKE %kw% OR applicant LIKE %kw%``）——实现沿用本模块
      "两步小查询"风格：审批表按提交人过滤 + agents 名称内存过滤，
      不写 SQL JOIN/OR。
    - ``status_counts``：各状态全量计数（不随筛选变化），顶部统计卡
      一次拿全，前端不用再调四次。
    """
    _require_reviewer(user_id)
    if status_filter not in (
        None,
        "all",
        STATUS_PENDING,
        STATUS_APPROVED,
        STATUS_REJECTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "status 仅支持 pending/approved/rejected/all"
                f"（不传 = 全部）: {status_filter}"
            ),
        )

    entries = await list_reviews(storage, status_filter)
    # 两步小查询：先拿审批记录，再按 id IN 查 agents 本体（不写 JOIN）
    agent_map: dict[str, AgentRow] = {}
    if entries:
        factory = getattr(storage, "_session_factory", None)
        if factory is not None:
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(AgentRow).where(
                            AgentRow.id.in_({e.agent_id for e in entries}),
                        ),
                    )
                ).scalars().all()
            agent_map = {r.id: r for r in rows}

    items: list[MarketReviewItemView] = []
    kw = (keyword or "").strip().lower()
    for e in entries:
        row = agent_map.get(e.agent_id)
        if row is None:
            continue  # 防御：孤儿审批行（agents 已删）不进列表
        data = (
            row.payload.get("data", {})
            if isinstance(row.payload, dict)
            else {}
        )
        name = str(data.get("name", ""))
        # 模糊搜索：名称 OR 提交人任一包含即命中（单字段双列 OR）
        if kw and kw not in name.lower() and kw not in e.applicant.lower():
            continue
        items.append(
            MarketReviewItemView(
                agent_id=e.agent_id,
                name=name,
                system_prompt=str(data.get("system_prompt", "")),
                applicant=e.applicant,
                status=e.status,
                reason=e.reason,
                reviewer=e.reviewer,
                department=e.department,
                system_name=e.system_name,
                tag=e.tag,
                description=e.description,
                reviewed_at=e.reviewed_at,
                created_at=e.created_at,
                updated_at=e.updated_at,
            ),
        )

    # 排序：待办先到先审（申请时间正序）；其余（含全部）申请时间倒序
    # （最近提交在前，与审核页"提交时间"列的阅读习惯一致）
    if status_filter == STATUS_PENDING:
        items.sort(key=lambda r: (r.created_at is None, r.created_at))
    else:
        items.sort(key=lambda r: (r.created_at is None, r.created_at), reverse=True)

    counts = await count_reviews_by_status(storage)
    total = len(items)
    start = (page_num - 1) * page_size
    return MarketReviewListResponse(
        reviews=items[start : start + page_size],
        total=total,
        status_counts={
            **counts,
            "all": sum(counts.values()),
        },
    )


@market_router.get(
    "/reviews/{agent_id}",
    response_model=MarketReviewItemView,
    summary="审核详情（发布表单 + 审核概况；设计阶段人人可查）",
)
async def get_market_review_detail(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketReviewItemView:
    """审核弹窗 / 审核详情弹窗的数据源：

    - **发布表单区块**：应用名称、提交人/时间、部门/系统、业务条线
      （tag）、发布说明——全是用户点发布时自己填写的，展示即可
      （名称从 agents 表现查，永远是最新内容）；
    - **审核概况区块**：状态、审批意见/驳回理由（reason，按状态区分
      文案——approved 存审批意见、rejected 存驳回理由）、审批人、
      审批时间——已通过/已驳回的"查看"复用本接口。

    待审核（pending）时审核概况字段为空串/null。无审批记录 404；
    设计阶段人人可查（带 X-User-ID 即可）。
    """
    review = await get_review_record(storage, agent_id)
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"该智能体没有发布申请记录: {agent_id}",
        )
    row = await _find_agent_row(storage, agent_id)
    data = (
        row.payload.get("data", {})
        if row is not None and isinstance(row.payload, dict)
        else {}
    )
    return MarketReviewItemView(
        agent_id=review.agent_id,
        name=str(data.get("name", "")),
        system_prompt=str(data.get("system_prompt", "")),
        applicant=review.applicant,
        status=review.status,
        reason=review.reason,
        reviewer=review.reviewer,
        department=review.department,
        system_name=review.system_name,
        tag=review.tag,
        description=review.description,
        reviewed_at=review.reviewed_at,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@market_router.post(
    "/reviews/{agent_id}/approve",
    response_model=MarketPublishStatusView,
    summary="审批通过（审批意见选填；设计阶段人人可审批）",
)
async def approve_market_review(
    agent_id: str,
    body: MarketApproveRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketPublishStatusView:
    """通过发布申请：审批记录置 approved，**同时插 ``agent_market``
    名单行**（有行 = 在市场）——从这一刻起智能体出现在市场列表/精选。

    ``reason`` 是**选填的审批意见**（通过可不填或写审批说明），落库到
    reason 字段——approved 状态下 reason 存审批意见而非拒绝理由，
    详情接口原样返回，前端按状态区分文案。

    仅 pending 状态可审批（已办结 409）；智能体不存在 404。
    审批自己的申请允许，靠审计日志留痕。
    """
    _require_reviewer(user_id)
    approve_reason = (body.reason or "").strip() if body is not None else ""
    record = await _find_agent_row(storage, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"智能体不存在: {agent_id}",
        )
    review = await decide_review(
        storage,
        agent_id,
        status=STATUS_APPROVED,
        reviewer=user_id,
        reason=approve_reason,
    )
    if review is None:
        existing = await get_review_record(storage, agent_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"该智能体没有发布申请记录: {agent_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该申请已办结（{existing.status}），仅待审批可操作。",
        )

    # 上架：插市场名单行（幂等——运营手动 INSERT 过则不重复插），
    # 发布档案（部门/系统/说明）随行复制；业务条线即 tag，走既有
    # tag 参数——上架后打标/撕标接口直接可用
    if await get_market_entry(storage, agent_id) is None:
        await insert_market_entry(
            storage,
            agent_id,
            tag=review.tag,
            meta={
                "department": review.department,
                "system_name": review.system_name,
                "description": review.description,
            },
        )
    log_audit(
        user_id,
        "approve_agent_publish",
        target=agent_id,
        detail=(
            f"审批通过（上架市场），申请人={review.applicant}"
            + (f"，审批意见={approve_reason}" if approve_reason else "")
        ),
    )
    return MarketPublishStatusView(**review.model_dump())


@market_router.post(
    "/reviews/{agent_id}/reject",
    response_model=MarketPublishStatusView,
    summary="审批拒绝（任何登录用户均可操作，理由必填）",
)
async def reject_market_review(
    agent_id: str,
    body: MarketRejectRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> MarketPublishStatusView:
    """拒绝发布申请：审批记录置 rejected，``reason`` 落库——申请人通过
    ``/agent/`` / ``/agent/owned`` 的 ``review_reason`` 看到理由，修改后
    重新 publish（旧结论被清空，回到 pending）。

    仅 pending 状态可审批（已办结 409）；不存在 404；非白名单 403；
    理由必填 1~200 字符（422）。
    """
    _require_reviewer(user_id)
    record = await _find_agent_row(storage, agent_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"智能体不存在: {agent_id}",
        )
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="拒绝理由不能为空。",
        )
    review = await decide_review(
        storage,
        agent_id,
        status=STATUS_REJECTED,
        reviewer=user_id,
        reason=reason,
    )
    if review is None:
        existing = await get_review_record(storage, agent_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"该智能体没有发布申请记录: {agent_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该申请已办结（{existing.status}），仅待审批可操作。",
        )
    log_audit(
        user_id,
        "reject_agent_publish",
        target=agent_id,
        detail=f"审批拒绝，申请人={review.applicant}，理由={reason}",
    )
    return MarketPublishStatusView(**review.model_dump())


__all__ = ["market_router"]
