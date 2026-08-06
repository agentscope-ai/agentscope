# -*- coding: utf-8 -*-
"""外部 skill hub 相关路由（迁移自 ``agentscope.app._router._workspace``）。

三个端点全部支持任意隔离策略（含 PER_SESSION）：workspace 一律通过
会话记录（DB 中持久化的 ``config.workspace_id``）解析，Bubblewrap 等
沙箱后端下精确指向对应会话的工作目录。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from agentscope._logging import logger
from agentscope.app._service import ResourceAccessService
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_skill_hubs,
    get_storage,
    get_workspace_manager,
)
from agentscope.app.hub import SkillHubBase
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase

from ..skills._schema import AgentSkillsListResponse, SkillActionResponse, SkillInfo

router = APIRouter(prefix="/workspace", tags=["skill-external"])


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
):
    """按会话记录解析其绑定的 workspace（含 PER_SESSION 语义）。

    从 DB 读取会话持久化的 ``config.workspace_id``，而非现算——
    沙箱后端据此定位对应会话的工作目录。
    """
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    return await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )


def _external_hub(
    hubs: dict[str, SkillHubBase],
) -> SkillHubBase:
    """取注册的外部 skillhub，未注册则 404。"""
    hub = hubs.get("external") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'external' skill hub is registered.",
        )
    return hub


def _set_token(hub: SkillHubBase, guwp_token: str | None) -> None:
    """逐请求刷新 hub 的 guwpToken（仅 ExternalSkillHub 有该方法）。"""
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)


async def _session_used_names(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
) -> set[str]:
    """返回会话 workspace 中已装备的 skill 名集合。

    session 无效 → 404；其他解析失败 → 降级为空集（不拖垮查询）。
    """
    try:
        workspace = await _resolve_workspace(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        agent_skills = await workspace.list_skills()
        return {s.name for s in agent_skills}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to resolve workspace for agent %s, marking no "
            "skills as used: %s",
            agent_id,
            e,
        )
        return set()


@router.get(
    "/skills/external",
    response_model=AgentSkillsListResponse,
    summary="Get Agent Skills",
    description=(
        "Query the external skillhub catalog and return it to the "
        "frontend, marking as ``used`` the skills already present in "
        "the session's workspace (``agent_id`` and ``session_id`` are "
        "passed as query parameters)."
    ),
)
async def get_agent_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    page: int = Query(default=0, ge=0),
    q: str = Query(default=""),
    size: int = Query(default=10, ge=1, le=200),
    sort: str = Query(default=""),
    label: str = Query(default=""),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AgentSkillsListResponse:
    """查询外部 skillhub 目录并返回。

    ``used`` 反映会话 workspace 已装备的 skill——从持久化会话记录
    解析（``session_id`` → ``config.workspace_id``），任意隔离策略
    （含 PER_SESSION）下都精确。
    """
    # 归属校验：agent 必须属于（或被共享给）调用者，否则 404。
    await access.resolve_agent(user_id, agent_id)

    used_names = await _session_used_names(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    # label 并入搜索关键字；sort 接受但忽略。
    merged_q = " ".join(filter(None, [label, q])) or None
    cursor = f"page:{page}" if page else None
    page_result = await hub.list_skills(
        user_id,
        q=merged_q,
        cursor=cursor,
        limit=size,
    )

    skills_list = [
        SkillInfo(
            name=card.name,
            category="public",
            description=card.description or "",
            used=card.name in used_names,
        )
        for card in page_result.cards
    ]
    return AgentSkillsListResponse(
        skills=skills_list,
        total=(
            page_result.total
            if page_result.total is not None
            else len(skills_list)
        ),
    )


@router.get(
    "/skills/uploaded",
    response_model=AgentSkillsListResponse,
    summary="Get Uploaded Skills",
    description=(
        "Query the external skillhub for the skills the caller uploaded "
        "and return them, marking as ``used`` the ones already present "
        "in the session's workspace (``agent_id`` and ``session_id`` "
        "are passed as query parameters)."
    ),
)
async def get_uploaded_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=5, ge=1, le=200),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AgentSkillsListResponse:
    """返回调用者上传到外部 skillhub 的 skill。

    端点按用户隔离，``guwpToken`` 必带——由其派生的会话 cookie 携带
    远程身份。``used`` 反映会话 workspace 已装备的 skill。
    """
    await access.resolve_agent(user_id, agent_id)

    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for uploaded skills.",
        )

    used_names = await _session_used_names(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)

    page_result = await hub.list_uploaded_skills(user_id, page=page, size=size)
    skills_list = [
        SkillInfo(
            name=card.name,
            category="public",
            description=card.description or "",
            used=card.name in used_names,
        )
        for card in page_result.cards
    ]
    return AgentSkillsListResponse(
        skills=skills_list,
        total=(
            page_result.total
            if page_result.total is not None
            else len(skills_list)
        ),
    )


@router.post(
    "/skill/download/{skill_full_name}",
    response_model=SkillActionResponse,
    summary="Enable Skill for Agent",
    description=(
        "Download a skill from the remote skillhub into the session's "
        "workspace. ``skill_full_name`` follows ``category:name`` "
        "(e.g. ``public:writing``); ``agent_id`` and ``session_id`` are "
        "passed as query parameters."
    ),
)
async def enable_agent_skill(
    skill_full_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillActionResponse:
    """为指定 agent 启用（下载安装）一个 skill。

    ``skill_full_name`` 遵循 ``category:name`` 约定（如
    ``public:writing``）；仅 ``public`` 可下载。已装备时幂等返回。
    目标 workspace 从持久化会话记录解析，任意隔离策略下都精确。
    """
    await access.resolve_agent(user_id, agent_id)

    if ":" not in skill_full_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "skill_full_name must be in 'category:name' form, "
                "e.g. 'public:writing'."
            ),
        )
    category, skill_name = skill_full_name.split(":", 1)
    if category != "public":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only 'public' skills can be enabled.",
        )

    # PER_SESSION 下 workspace id 是每会话随机值，必须来自数据库而非现算。
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    # 已装备 —— 幂等成功。按 agent-facing 名或目录名匹配（frontmatter
    # 名可能与 slug 不同）。
    existing = await workspace.list_skills()
    if skill_name in {s.name for s in existing} or any(
        os.path.basename(s.dir.rstrip("/\\")) == skill_name
        for s in existing
    ):
        logger.info(
            "Skill '%s' already equipped in agent %s's workspace",
            skill_full_name,
            agent_id,
        )
        return SkillActionResponse(
            success=True,
            action="enabled",
            skill_id=skill_full_name,
        )

    # 经 hub 抽象下载 —— 归档流式送入 workspace 后端（沙箱兼容）。
    hub = _external_hub(hubs)
    _set_token(hub, guwp_token)
    try:
        archive = await hub.download(user_id, skill_name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found on the remote skillhub.",
        ) from None
    try:
        await workspace.add_skill_archive(
            archive.stream,
            archive.format,
            skill_name,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to install skill '{skill_name}': {e}",
        ) from e

    # 校验确有新 skill 落盘（解压目录可能按 frontmatter 命名）。
    refreshed = await workspace.list_skills()
    new_names = {s.name for s in refreshed} - {s.name for s in existing}
    if not new_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Downloaded skill '{skill_name}' has no valid SKILL.md "
                "(requires 'name' and 'description' fields)."
            ),
        )

    logger.info("Enabled skill '%s' for agent '%s'", skill_full_name, agent_id)
    return SkillActionResponse(
        success=True,
        action="enabled",
        skill_id=skill_full_name,
    )
