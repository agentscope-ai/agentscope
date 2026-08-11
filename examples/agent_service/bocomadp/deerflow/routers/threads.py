# -*- coding: utf-8 -*-
"""DeerFlow 风格 threads 管理端点（最小集：仅支撑前端对话闭环）。

对齐 deer-flow 2.0 ``backend/app/gateway/routers/threads.py`` 的 LangGraph
SDK 调用契约，但只实现对话闭环必需的最小端点：

- ``POST /api/threads``             创建 thread（仅生成 id，session 懒创建）
- ``POST /api/threads/search``      列表查询（恒空列表——thread 实体由首次
  run 懒创建，无独立注册表，列表页仅需不报错）
- ``GET  /api/threads/{tid}/state``   读取最新状态（``values.messages``）
- ``POST /api/threads/{tid}/history`` 读取最近 checkpoint（``values.messages``）

未实现（非对话必需）：删除 / 重命名 / 状态更新 / 分页游标。

设计要点：

- thread_id 与原生 session_id 同一资源；session 由 ``runs/stream`` 端点
  首次运行时自动创建（见 ``deerflow_chat._ensure_session``），因此 create
  端点无状态、search 无注册表可查。
- history/state 从原生 storage 的 session 消息重建 LangGraph State，供
  SDK 的 ``useStream`` 初始化界面与 ``onFinish`` 收尾（values.messages
  为唯一渲染数据源，见 LangGraph SDK 补丁机制）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agentscope._utils._common import _generate_id
from agentscope.app._service import ChatService
from agentscope.app.deps import get_chat_service, get_storage
from agentscope.app.storage import StorageBase
from agentscope.message import Msg, ToolCallState

from bocomadp.config import load_agents_from_yaml

from ..deps import _default_agent_id, get_deerflow_user_id, get_run_manager
from ..formatter import build_confirm_card
from ..runs import RunManager, RunStatus

logger = logging.getLogger(__name__)

threads_router = APIRouter(prefix="/api/threads", tags=["deerflow-threads"])

# ── 消息分页（对齐 deer-flow 2.0 ``/api/threads/{tid}/messages/page``）──

# 全量拉取的批大小：storage.list_messages 直接把 limit 透传给 SQL LIMIT，
# 无框架级上限；500 一批避免极端大会话单次查询过重。
_MESSAGE_PAGE_FETCH_BATCH = 500


def _msg_to_langgraph(msg: Msg) -> dict[str, Any]:
    """原生 Msg → LangGraph 消息（type/content 块数组，仅保留 text 块）。

    role 映射：user→human / assistant→ai / system→system；content 无文本
    块时为空字符串（前端渲染兜底）。
    """
    role_map = {"user": "human", "assistant": "ai", "system": "system"}
    blocks = [
        {"type": "text", "text": block.text}
        for block in msg.content
        if getattr(block, "type", None) == "text"
    ]
    return {
        "type": role_map.get(msg.role, "human"),
        # id 供前端历史分页去重（messageIdentity 以 content.id 为身份键）
        "id": msg.id,
        "content": blocks if blocks else "",
    }


async def _load_messages(storage: StorageBase, user_id: str,
                        thread_id: str, limit: int) -> list[Msg]:
    """读取会话最近消息；session 不存在 / 存储异常时返回空列表。"""
    try:
        messages, _ = await storage.list_messages(user_id, thread_id,
                                                  limit=limit)
    except Exception:  # noqa: BLE001 —— 只读端点，存储异常不阻断对话
        logger.exception(
            "deerflow: failed to load messages for thread %s",
            thread_id,
        )
        return []
    return messages


def _agent_ids() -> list[str]:
    """config.yaml seed agents + 默认 agent（去重，供 session 遍历）。

    thread_id == session_id，而 session 归属某个 agent（session 记录以
    (user, agent) 分片），读取会话前需先枚举候选 agent。
    """
    try:
        agent_ids = [entry.agent_id for entry in load_agents_from_yaml()]
    except Exception:  # noqa: BLE001 —— config 不可读时仅用默认 agent
        agent_ids = []
    if _default_agent_id() not in agent_ids:
        agent_ids.append(_default_agent_id())
    return agent_ids


def _thread_title(record: Any) -> str:
    """会话标题：最近一条用户消息文本（截断 50 字符）。

    无用户消息时回退 session 展示名（默认创建时间戳），再回退
    Untitled——与前端 ``titleOfThread`` 的 ``values.title ?? "Untitled"``
    语义对齐。
    """
    state = getattr(record, "state", None)
    context = getattr(state, "context", None) or []
    for msg in reversed(context):
        if getattr(msg, "role", None) != "user":
            continue
        text = msg.get_text_content() or ""
        if text.strip():
            return text.strip()[:50]
    config = getattr(record, "config", None)
    name = getattr(config, "name", "") if config is not None else ""
    return name or "Untitled"


async def _pending_confirm_cards(
    storage: StorageBase,
    user_id: str,
    thread_id: str,
) -> list[dict[str, Any]]:
    """会话仍等待确认（ASKING）时，重建前端确认卡片 tool 消息。

    确认卡片只在流式阶段以 messages 帧下发，不持久化；刷新页面后
    session 仍 park 在 ASKING（等待用户确认），而消息列表里没有卡片——
    用户无法完成确认，发新消息又会命中"无 REPLY_END"的挂死路径。故在
    读端点按 session state 重建卡片（结构与流式翻译完全一致），供
    前端 HumanInputCard 重新渲染。
    """
    cards: list[dict[str, Any]] = []
    for agent_id in _agent_ids():
        try:
            session_record = await storage.get_session(
                user_id,
                agent_id,
                thread_id,
            )
        except Exception:  # noqa: BLE001 —— 单个 agent 查询失败不阻断其余
            logger.exception(
                "deerflow: failed to load session %s (agent=%s) for "
                "confirm-card rebuild",
                thread_id,
                agent_id,
            )
            continue
        if session_record is None:
            continue
        agent_record = await storage.get_agent(user_id, agent_id)
        agent_name = (
            agent_record.data.name if agent_record is not None else agent_id
        )
        for tc in session_record.state.get_awaiting_tool_calls(agent_name):
            if tc.state != ToolCallState.ASKING:
                continue
            cards.append(build_confirm_card(tc.model_dump(mode="json")))
        break
    return cards


# ── 请求模型 ──────────────────────────────────────────────────────────


class CreateThreadRequest(BaseModel):
    """创建 thread 的请求体（LangGraph SDK ``threads.create`` 契约）。"""

    thread_id: str | None = None
    """客户端预置 id；缺省时服务端生成。"""
    metadata: dict | None = None
    """接受但忽略：本方案无 thread 元数据存储。"""
    if_exists: str | None = None
    """接受但忽略：create 恒幂等（懒创建，无实体可冲突）。"""


class ThreadHistoryRequest(BaseModel):
    """thread history 请求体（LangGraph SDK ``getHistory`` 契约）。"""

    limit: int | None = None
    """返回 checkpoint 条数上限；本方案最多返回最近一条。"""
    before: str | None = None
    """接受但忽略：无 checkpoint 游标（非对话必需）。"""
    metadata: dict | None = None
    """接受但忽略。"""


class ThreadSearchRequest(BaseModel):
    """thread search 请求体（LangGraph SDK ``threads.search`` 契约）。"""

    limit: int = 10
    """返回条数上限。"""
    offset: int = 0
    """跳过条数（向前分页游标）。"""
    metadata: dict | None = None
    """接受但忽略：无 thread 元数据可过滤。"""
    status: str | None = None
    """接受但忽略：无 thread 状态注册表。"""
    sortBy: str | None = None
    """接受但忽略：恒按 updated_at 降序（前端只传该值）。"""
    sortOrder: str | None = None
    """接受但忽略：恒降序。"""
    select: list[str] | None = None
    """接受但忽略：恒返回全量字段。"""


# ── 端点 ──────────────────────────────────────────────────────────────


@threads_router.post("", summary="Create a thread")
async def create_thread(
    body: CreateThreadRequest | None = None,
) -> dict[str, str]:
    """生成 thread_id（= 原生 session_id）；session 实体懒创建于首次 run。"""
    thread_id = body.thread_id if body and body.thread_id else _generate_id()
    return {"thread_id": thread_id}


@threads_router.post("/search", summary="Search threads")
async def search_threads(
    body: ThreadSearchRequest | None = None,
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
) -> list[dict[str, Any]]:
    """列出该用户的会话（左侧历史会话列表的数据源）。

    会话实体随首次 run 懒创建、无独立注册表，这里从 storage 的 session
    记录聚合（thread_id == session_id）；按 updated_at 降序 + offset/limit
    分页，响应结构对齐 deer-flow 原 ``ThreadResponse``（thread_id / status /
    created_at / updated_at / metadata / values.title）。
    """
    limit = body.limit if body else 10
    offset = body.offset if body else 0

    # 跨 agent 聚合 session；同一 thread 只属于一个 agent，但保险起见
    # 按 thread_id 去重（保留 updated_at 最新的一条）。
    records: dict[str, Any] = {}
    for agent_id in _agent_ids():
        try:
            sessions = await storage.list_sessions(user_id, agent_id)
        except Exception:  # noqa: BLE001 —— 单个 agent 查询失败不阻断其余
            logger.exception(
                "deerflow: failed to list sessions for agent %s",
                agent_id,
            )
            continue
        for record in sessions:
            existing = records.get(record.id)
            if existing is None or record.updated_at > existing.updated_at:
                records[record.id] = record

    ordered = sorted(
        records.values(),
        key=lambda record: record.updated_at,
        reverse=True,
    )
    page = ordered[offset : offset + limit]
    return [
        {
            "thread_id": record.id,
            "status": "idle",
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "metadata": {},
            # 标题取最近一条用户消息文本（截断）；无消息时回退
            # session 展示名（默认创建时间戳）或 Untitled。
            "values": {"title": _thread_title(record)},
            "interrupts": {},
        }
        for record in page
    ]


@threads_router.delete("/{thread_id}", summary="Delete a thread")
async def delete_thread(
    thread_id: str,
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
    chat_service: ChatService = Depends(get_chat_service),
    run_manager: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """删除会话及其全部消息（左侧历史列表的删除按钮）。

    thread_id == session_id，session 以 (user, agent) 分片：遍历候选
    agent 找到归属的 session，先中断其活跃 run（若有，避免后台任务
    继续写已删除的 session）再删除。未找到时幂等返回成功——前端删除
    后无需区分"已不存在"。
    """
    for agent_id in _agent_ids():
        try:
            session_record = await storage.get_session(
                user_id,
                agent_id,
                thread_id,
            )
        except Exception:  # noqa: BLE001 —— 单个 agent 查询失败不阻断其余
            logger.exception(
                "deerflow: failed to load session %s (agent=%s) for delete",
                thread_id,
                agent_id,
            )
            continue
        if session_record is None:
            continue

        # 中断活跃 run（若有）：RunManager 置 interrupted + 原生 interrupt
        record = run_manager.get_by_session(thread_id)
        if record is not None and record.active:
            run_manager.mark_finished(record.run_id, RunStatus.INTERRUPTED)
        try:
            await chat_service.interrupt(user_id, thread_id, agent_id)
        except Exception:  # noqa: BLE001 —— 无活跃 run / agent 未注册时忽略
            logger.debug(
                "deerflow: interrupt before delete skipped for thread %s",
                thread_id,
            )

        deleted = await storage.delete_session(user_id, agent_id, thread_id)
        return {
            "success": deleted,
            "message": (
                f"thread {thread_id} deleted"
                if deleted
                else f"thread {thread_id} not found"
            ),
        }

    # 未找到归属 agent：幂等返回成功
    return {"success": True, "message": f"thread {thread_id} not found"}


@threads_router.get("/{thread_id}/state", summary="Get thread state")
async def get_thread_state(
    thread_id: str,
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """读取会话最新消息，组装 LangGraph State（``values.messages``）。

    供 SDK ``getState`` 使用（页面恢复 / 流结束后状态拉取）；session
    不存在时返回空 messages（前端渲染为空对话）。
    """
    messages = await _load_messages(storage, user_id, thread_id, limit=50)
    cards = await _pending_confirm_cards(storage, user_id, thread_id)
    return {
        "values": {
            "messages": [_msg_to_langgraph(m) for m in messages] + cards,
        },
        "next": [],
        "tasks": [],
        "checkpoint_id": None,
        "metadata": {},
    }


def _is_middleware_message(msg: Msg) -> bool:
    """中间件内部消息（``metadata.caller`` 以 ``middleware:`` 开头）不进历史页。

    与 deer-flow 后端 ``_scan_thread_message_page`` 的可见性规则对齐。
    """
    return str(msg.metadata.get("caller", "")).startswith("middleware:")


def _msg_to_run_message(msg: Msg, seq: int) -> dict[str, Any]:
    """原生 Msg → deer-flow RunMessage 行。

    run_id 取原生 msg id（bocomadp 无 run 概念，消息即最小单元）；
    ``content.id`` 同取 msg id——前端按该 id 去重/合并历史与实时消息。
    """
    return {
        "run_id": msg.id,
        "seq": seq,
        "content": _msg_to_langgraph(msg),
        "metadata": {"caller": str(msg.metadata.get("caller", ""))},
        "created_at": msg.created_at,
    }


@threads_router.get(
    "/{thread_id}/messages/page",
    summary="Get thread messages page",
)
async def list_thread_messages_page(
    thread_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before_seq: int | None = Query(default=None, ge=1),
    after_seq: int | None = Query(default=None),
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """返回一页线程消息（对齐 deer-flow 2.0 同路径端点契约）。

    - 仅支持向后分页：``before_seq`` 为线程全局消息序号游标，不带则取
      最新一页；传 ``after_seq`` 返回 422（与 deer-flow 行为一致）。
    - bocomadp 的会话消息没有持久化序号，seq 由"每次请求从会话头全量
      编号"派生——消息顺序固定，seq 天然稳定且无需额外存储；会话消息
      量在万级以内时全量拉取的开销可接受。
    - 响应 ``{data, has_more, next_before_seq}``：``data`` 按 seq 升序，
      ``has_more`` 为真时 ``next_before_seq`` 即更早一页的游标。
    """
    if after_seq is not None:
        raise HTTPException(
            status_code=422,
            detail="after_seq is not supported by this backward-only endpoint",
        )

    # 全量拉取会话消息（旧→新），过滤中间件消息后按 1 起编号。
    visible: list[Msg] = []
    before: str | None = None
    while True:
        messages, has_more = await storage.list_messages(
            user_id,
            thread_id,
            limit=_MESSAGE_PAGE_FETCH_BATCH,
            before=before,
        )
        if not messages:
            break
        visible.extend(m for m in messages if not _is_middleware_message(m))
        if not has_more:
            break
        before = messages[0].id  # 本批最早一条；继续向前取更早的消息

    # 定位分页窗口：seq < before_seq（或最新一页）的最后 limit 条。
    total = len(visible)
    end = total if before_seq is None else min(before_seq - 1, total)
    start = max(0, end - limit)
    page = visible[start:end]

    has_more = start > 0
    rows = [
        _msg_to_run_message(msg, start + idx + 1)
        for idx, msg in enumerate(page)
    ]
    if before_seq is None:
        # 最新一页追加重建的确认卡片（仅当 session 仍等待确认）
        cards = await _pending_confirm_cards(storage, user_id, thread_id)
        for card in cards:
            rows.append(
                {
                    "run_id": "confirm-card",
                    "seq": start + len(rows) + 1,
                    "content": card,
                    "metadata": {"caller": "agent_scope_permission"},
                    "created_at": "",
                },
            )
    return {
        "data": rows,
        "has_more": has_more,
        "next_before_seq": start + 1 if has_more else None,
    }


@threads_router.post("/{thread_id}/history", summary="Get thread history")
async def get_thread_history(
    thread_id: str,
    body: ThreadHistoryRequest | None = None,
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
) -> list[dict[str, Any]]:
    """返回最近一个 checkpoint（含 ``values.messages``）。

    供 SDK ``getHistory`` 使用（``useStream`` 挂载时初始化界面、流结束
    ``onFinish`` 前拉取最终状态）；会话为空时返回空数组。
    """
    limit = body.limit if body and body.limit else 10
    messages = await _load_messages(storage, user_id, thread_id, limit=limit)
    cards = await _pending_confirm_cards(storage, user_id, thread_id)
    if not messages and not cards:
        return []
    return [
        {
            "checkpoint_id": _generate_id(),
            "values": {
                "messages": [_msg_to_langgraph(m) for m in messages] + cards,
            },
            "metadata": {},
        },
    ]


__all__ = ["threads_router"]
