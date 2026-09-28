# -*- coding: utf-8 -*-
"""技能上传 / 卸载写穿 ``agents_extend``（框架路由包装）。

背景
----
``/skills/external``、``/skills/uploaded`` 的 ``used`` 标记以 ``agents_extend``
为准（见 :mod:`bocomadp.routers.skill_router`）。框架自带的两条写路径只动沙箱：

- ``POST /workspace/skill/upload`` —— 上传本地技能目录；
- ``DELETE /workspace/skill/{skill_name}`` —— 卸载。

不补写就会出现"上传了仍显示未安装 / 卸载了仍显示已安装"。本模块在
``create_app()`` 之后**前插**两条同路径路由做包装（与 ``session_default_mode``
/ ``install_skill_list_from_table`` 同一套模式，**不改 ``src/agentscope``**）。

做法
----
包装**先原样调用框架的 handler 函数**，成功后再更新表，因此：

- 状态码 / 响应体 / 异常与原实现**逐字一致**（upload → 201 空体、
  delete → 204 空体；校验 422、会话 404 等都由框架 handler 自己抛）；
- 表更新失败只打 warning —— 技能已经在沙箱里落盘/删除，缓存问题不能让
  请求失败。

表更新口径：

- **upload**：成功后在同一个会话上按框架 ``list_skills`` 拿一次沙箱真值
  （与 ``GET /workspace/skill`` 同源），把清单里缺的项补进表；**没有行时
  建行**，条目一律 ``type="uploaded"``（上传路径的约定）；
- **delete**：把名字匹配的条目从表里删掉（:func:`entry_names` 口径 ——
  frontmatter 名 / 目录名 / ``namespace:name`` 都能删中）；没有行或没匹配到
  就不动表。
"""
from __future__ import annotations

from typing import Any

from fastapi import (
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)
from fastapi.routing import APIRoute

from agentscope._logging import logger
from agentscope.app._router._workspace import (
    list_skills as _framework_list_skills,
)
from agentscope.app._router._workspace import (
    remove_skill as _framework_remove_skill,
)
from agentscope.app._router._workspace import (
    upload_skill as _framework_upload_skill,
)
from agentscope.app.deps import (
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase

from bocomadp.agents_extend_store import (
    remove_extend_entries,
    sync_extend_from_names,
)

#: ``app.state`` 上的安装标记（幂等用）。
_INSTALLED_FLAG = "_bocomadp_skill_write_through_installed"


async def upload_skill_write_through(
    manifest: str = Form(
        description=(
            "JSON ``{entries: [{path, size}]}`` describing the parts, "
            "in the order they are sent."
        ),
    ),
    files: list[UploadFile] = File(description="The folder's files."),
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """``POST /workspace/skill/upload`` 包装：行为同框架 + 写穿扩展表。

    签名与框架 handler 逐字一致，保证 multipart 校验与 OpenAPI 不变。
    """
    # ① 原样调用框架实现：manifest 校验、tar 流、写入沙箱、异常全保持原样。
    await _framework_upload_skill(
        manifest=manifest,
        files=files,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        storage=storage,
        workspace_manager=workspace_manager,
    )
    # ② 成功后补表：按框架查询接口拿一次沙箱真值，增量补齐（无行则建行）。
    try:
        skills = await _framework_list_skills(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            storage=storage,
            workspace_manager=workspace_manager,
        )
        changed = await sync_extend_from_names(
            storage,
            agent_id,
            [getattr(skill, "name", "") for skill in skills],
            source="skill-upload",
        )
        logger.info(
            "skill upload: agents_extend synced for agent %s "
            "(listed=%d, changed=%s)",
            agent_id,
            len(skills),
            changed,
        )
    except Exception:  # noqa: BLE001 —— 缓存补写绝不能影响上传结果
        logger.warning(
            "skill upload: failed to sync agents_extend for agent %s",
            agent_id,
            exc_info=True,
        )


async def remove_skill_write_through(
    skill_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """``DELETE /workspace/skill/{skill_name}`` 包装：行为同框架 + 删表内条目。"""
    # ① 原样调用框架实现（失败时异常与原来一致，表也不动）。
    await _framework_remove_skill(
        skill_name=skill_name,
        agent_id=agent_id,
        session_id=session_id,
        user_id=user_id,
        storage=storage,
        workspace_manager=workspace_manager,
    )
    # ② 成功后把匹配的条目删掉（无行 / 未匹配 → 不动表）。
    try:
        removed = await remove_extend_entries(
            storage,
            agent_id,
            skill_name,
            source="skill-remove",
        )
        logger.info(
            "skill remove: agents_extend updated for agent %s (%s, changed=%s)",
            agent_id,
            skill_name,
            removed,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "skill remove: failed to update agents_extend for agent %s",
            agent_id,
            exc_info=True,
        )


def install_skill_write_through(app: Any) -> None:
    """前插两条写穿路由（幂等）。

    **必须在** ``root_app.mount("/api", app)`` **之前调用**（mount 之后路由
    不再挂在同一个 router 上，前插无效），与
    :func:`bocomadp.routers.skill_router.install_skill_list_from_table` 同序。

    Args:
        app (`Any`):
            FastAPI 应用（``create_app()`` 的返回值，尚未挂到 ``/api``）。
    """
    if getattr(app.state, _INSTALLED_FLAG, False):
        return
    app.router.routes.insert(
        0,
        APIRoute(
            # 与框架 workspace_router 的 prefix="/workspace" + "/skill/upload" 同路径
            "/workspace/skill/upload",
            upload_skill_write_through,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            summary=(
                "Install a skill from an uploaded folder "
                "(agents_extend write-through)"
            ),
            name="upload_skill_write_through",
        ),
    )
    app.router.routes.insert(
        0,
        APIRoute(
            "/workspace/skill/{skill_name}",
            remove_skill_write_through,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            summary=(
                "Remove a skill from the session's workspace "
                "(agents_extend write-through)"
            ),
            name="remove_skill_write_through",
        ),
    )
    setattr(app.state, _INSTALLED_FLAG, True)
    logger.info(
        "installed agents_extend write-through for POST "
        "/workspace/skill/upload and DELETE /workspace/skill/{skill_name}",
    )


__all__ = [
    "install_skill_write_through",
    "remove_skill_write_through",
    "upload_skill_write_through",
]
