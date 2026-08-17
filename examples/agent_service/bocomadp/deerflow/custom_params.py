# -*- coding: utf-8 -*-
"""请求级自定义参数（custom_params）上下文与持久化模块。

对齐 deer-flow 新分支的 ``custom_params`` 机制：run/stream 请求体携带
``custom_params``（空间码、用户编码等），路由层在 spawn 后台 run 任务前
经 ContextVar 注入（``asyncio.create_task`` 复制当前上下文，值随之传播
到 run 任务内），工具侧中间件读取后强制覆盖模型传参，避免在每个
tool call 中重复解析请求。

持久化（对齐 deer-flow ``lead_agent/agent.py`` 的 ``_save_custom_params`` /
``_load_custom_params``）：请求携带 custom_params 时落盘到会话绑定的
workspace（``sessions/<session_id>/custom_params.json``），后续请求
（如 HITL 确认续跑）未携带时从落盘文件回退加载，保证空间码约束
在会话生命周期内持续生效。落盘/读盘均为非致命操作，失败仅告警。

原生 ``/chat/`` 路径不携带 custom_params，ContextVar 保持默认空 dict，
下游行为与现状一致。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger(__name__)

_custom_params_ctx: ContextVar[dict[str, Any]] = ContextVar(
    "custom_params",
    default={},
)

#: workspace 标准布局中 per-session 分区目录（与框架
#: ``agentscope.workspace._utils.DEFAULT_SESSIONS_DIR`` 一致）。
_SESSIONS_DIR = "sessions"

#: 会话 workspace 内的 custom_params 落盘文件名。
_CUSTOM_PARAMS_FILENAME = "custom_params.json"


def set_custom_params(params: dict[str, Any] | None) -> Token:
    """设置当前上下文的自定义参数，返回 reset 用的 token。

    路由层在 spawn 后台任务前调用；spawn 完成后调用
    :func:`reset_custom_params` 恢复——``asyncio.create_task`` 已复制
    上下文快照，reset 不影响已创建的后台任务。
    """
    return _custom_params_ctx.set(params or {})


def reset_custom_params(token: Token) -> None:
    """恢复 ContextVar 到 :func:`set_custom_params` 之前的值。"""
    _custom_params_ctx.reset(token)


def get_custom_params() -> dict[str, Any]:
    """获取当前上下文的自定义参数（工具中间件中调用）。"""
    return _custom_params_ctx.get()


def _custom_params_path(workspace: Any, session_id: str) -> str:
    """会话 workspace 内 custom_params 落盘文件的 backend 侧路径。

    位于 workspace 标准布局 ``sessions/<session_id>/`` 下（与
deer-flow 的 ``threads/{thread_id}/custom_params.json`` 对应）；路径
    经 backend 的 ``join_path`` 拼接，任意后端（Local / Docker /
    Sandbox）的路径语义均正确。
    """
    backend = workspace.get_backend()
    return backend.join_path(
        workspace.workdir,
        _SESSIONS_DIR,
        session_id,
        _CUSTOM_PARAMS_FILENAME,
    )


async def save_custom_params_to_workspace(
    workspace: Any,
    session_id: str,
    params: dict[str, Any],
) -> None:
    """落盘 custom_params 到会话 workspace（非致命：失败仅告警）。

    对齐 deer-flow ``_save_custom_params``：写盘失败不阻断 run 创建——
    ContextVar 注入已携带本次请求值，落盘仅为后续请求的回退兜底。
    """
    try:
        path = _custom_params_path(workspace, session_id)
        await workspace.get_backend().write_file(
            path,
            json.dumps(params, ensure_ascii=False).encode("utf-8"),
        )
        logger.info(
            "deerflow: saved custom_params for session %s: %s",
            session_id,
            params,
        )
    except Exception:  # noqa: BLE001 —— 非致命，仅告警
        logger.warning(
            "deerflow: failed to save custom_params for session %s "
            "(non-fatal)",
            session_id,
            exc_info=True,
        )


async def load_custom_params_from_workspace(
    workspace: Any,
    session_id: str,
) -> dict[str, Any] | None:
    """从会话 workspace 读回落盘的 custom_params（非致命）。

    文件不存在返回 None（调用方降级为空 dict）；读取或解析失败仅
    告警并返回 None，不阻断 run 创建。
    """
    try:
        backend = workspace.get_backend()
        path = _custom_params_path(workspace, session_id)
        if not await backend.file_exists(path):
            return None
        data = json.loads((await backend.read_file(path)).decode("utf-8"))
        if not isinstance(data, dict):
            logger.warning(
                "deerflow: custom_params for session %s is not a JSON "
                "object, ignored",
                session_id,
            )
            return None
        logger.info(
            "deerflow: loaded custom_params for session %s: %s",
            session_id,
            data,
        )
        return data
    except Exception:  # noqa: BLE001 —— 非致命，仅告警
        logger.warning(
            "deerflow: failed to load custom_params for session %s "
            "(non-fatal)",
            session_id,
            exc_info=True,
        )
        return None


__all__ = [
    "set_custom_params",
    "reset_custom_params",
    "get_custom_params",
    "save_custom_params_to_workspace",
    "load_custom_params_from_workspace",
]
