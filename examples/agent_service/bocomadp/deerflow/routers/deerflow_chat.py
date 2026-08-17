# -*- coding: utf-8 -*-
"""DeerFlow 风格 SSE 路由（threads/runs 资源模型）。

对齐 deer-flow 2.0 ``backend/app/gateway/routers/thread_runs.py`` 的 4 个
端点，但执行引擎复用原生 ``ChatService``（配置与原生 ``/chat/`` 完全
一致——agent 构建、模型、工具、审计中间件、HITL 全部同源）：

- ``POST /api/threads/{tid}/runs/stream``  创建 run + SSE 流式
- ``POST /api/threads/{tid}/runs/wait``    创建 run + 阻塞至完成
- ``GET  /api/threads/{tid}/runs/{rid}/stream``  join 已有 run（回放 + live）
- ``POST /api/threads/{tid}/runs/{rid}/cancel``  取消（映射原生 session 级 interrupt）

设计要点（方案决策①④⑤）：

- thread_id == session_id（同一资源）；run_id 由 RunManager 预生成，
  ``Content-Location`` 头可提前填充。
- 并发 409 复用 ``ChatRunRegistry.spawn`` + 分布式锁；cancel 映射原生
  ``ChatService.interrupt``（一个 session 至多一个 run，run 级 == session 级）。
- 断线默认 ``on_disconnect=cancel``：检测到断线后调用原生 interrupt，
  停止后台任务不再消耗模型额度；``continue`` 时仅断开订阅，run 继续。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextvars import Token
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app.deps import (
    get_chat_run_registry,
    get_chat_service,
    get_storage,
    get_workspace_manager,
)
from agentscope.app._manager import ChatRunRegistry
from agentscope.app._service import ChatService
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    StorageBase,
)
from agentscope.app.workspace_manager import WorkspaceManagerBase
from agentscope.credential import CredentialFactory
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import Msg, TextBlock

from bocomadp.config import load_agents_from_yaml, load_models_from_yaml

from ..bridge import BusBridge
from ..auth_context import (
    reset_resolved_auth,
    resolve_auth_params,
    set_resolved_auth,
)
from ..custom_params import (
    load_custom_params_from_workspace,
    reset_custom_params,
    save_custom_params_to_workspace,
    set_custom_params,
)
from ..deps import (
    _default_agent_id,
    get_bridge,
    get_deerflow_user_id,
    get_run_manager,
)
from ..protocol import (
    END_SENTINEL,
    EVENT_CUSTOM,
    EVENT_ERROR,
    EVENT_MESSAGES,
    StreamEvent,
    format_sse,
)
from ..runs import RunManager, RunRecord, RunStatus

logger = logging.getLogger(__name__)

# LangGraph 消息 type → 原生 Msg.role（前端 SDK 固定发 human）。
_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system"}


class _HumanInputResponseMarker:
    """human 消息携带 ``human_input_response`` 的标记。

    前端确认卡片（HumanInputCard）应答通过一条 ``hide_from_ui`` 的
    human 消息提交，``additional_kwargs.human_input_response`` 携带应答
    载荷；后端据此构造 :class:`UserConfirmResultEvent` 续跑（Case B）。
    保留原始消息 dict 以便匹配失败时回退为普通消息处理。
    """

    def __init__(self, response: dict, raw: dict) -> None:
        self.response = response
        self.raw = raw


def _extract_human_input_response(raw: dict) -> dict | None:
    """提取 human 消息 ``additional_kwargs.human_input_response``。

    仅识别 ``kind == "human_input_response"`` 的载荷（与前端
    [human-input.ts](file:///home/llm/zhangle/agentscope-workspace/deer-flow-2.0/frontend/src/core/messages/human-input.ts)
    的 :func:`parseHumanInputResponse` 对齐），其余消息返回 None。
    """
    if str(raw.get("type", "")) != "human":
        return None
    additional_kwargs = raw.get("additional_kwargs")
    if not isinstance(additional_kwargs, dict):
        return None
    response = additional_kwargs.get("human_input_response")
    if isinstance(response, dict) and response.get(
        "kind",
    ) == "human_input_response":
        return response
    return None


def _langgraph_message_to_msg(raw: dict) -> Msg:
    """LangGraph 消息 dict（type/content）→ 原生 Msg。

    type: human→user / ai→assistant / system→system；content 支持字符串
    或块数组（仅保留 text 块）；additional_kwargs 等扩展字段忽略。
    """
    role = _ROLE_MAP.get(str(raw.get("type", "human")))
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported LangGraph message type {raw.get('type')!r}.",
        )
    content = raw.get("content")
    if isinstance(content, str):
        blocks = [TextBlock(text=content)]
    elif isinstance(content, list):
        blocks = [
            TextBlock(text=block["text"])
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    else:
        blocks = []
    return Msg(name=role, role=role, content=blocks)


def _convert_input(raw: Any) -> Any:
    """SDK input（dict / LangGraph 消息）→ 原生 ChatRequest.input 形态。

    优先检测前端确认卡片应答（human 消息携带 human_input_response），
    返回 :class:`_HumanInputResponseMarker` 供路由层构造确认事件；其余
    消息按原逻辑转换。
    """
    if raw is None or isinstance(raw, Msg):
        return raw
    if isinstance(raw, (UserConfirmResultEvent, ExternalExecutionResultEvent)):
        return raw
    if isinstance(raw, list):
        return [_convert_input(item) for item in raw]
    if isinstance(raw, dict):
        response = _extract_human_input_response(raw)
        if response is not None:
            return _HumanInputResponseMarker(response, raw)
        messages = raw.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    response = _extract_human_input_response(message)
                    if response is not None:
                        return _HumanInputResponseMarker(response, message)
            return [_langgraph_message_to_msg(m) for m in messages]
        if isinstance(raw.get("type"), str) and "content" in raw:
            return _langgraph_message_to_msg(raw)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported input dict: expected {'messages': [...]} or "
                f"a single LangGraph message, got keys {list(raw)}."
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported input type {type(raw).__name__}.",
    )


def _msg_to_human_chunk(msg: Msg) -> dict[str, Any]:
    """原生 Msg（用户输入）→ LangGraph human 消息 chunk。

    与 threads.py ``_msg_to_langgraph`` 同构（type/id/content 块数组）；
    id 取 Msg.id——与 ``ChatService.run`` 持久化进 storage 的是同一实例，
    刷新/join 后前端按 id 去重不会出现第二条。
    """
    blocks = [
        {"type": "text", "text": block.text}
        for block in msg.content
        if isinstance(block, TextBlock)
    ]
    return {
        "type": "human",
        "id": msg.id,
        "content": blocks if blocks else "",
    }


def _collect_human_chunks(input_msg: Any) -> list[dict[str, Any]]:
    """从转换后的输入提取用户消息 chunk（Case A 回显；事件续跑无）。"""
    msgs = (
        input_msg
        if isinstance(input_msg, list)
        else [input_msg]
        if isinstance(input_msg, Msg)
        else []
    )
    return [_msg_to_human_chunk(m) for m in msgs]


async def _load_human_chunks(
    storage: StorageBase,
    user_id: str,
    thread_id: str,
) -> list[dict[str, Any]]:
    """join 场景回显：从 storage 读最近用户消息 → human chunks。

    断线重连（SDK joinStream）时前端乐观消息尚未清理，而回放日志无
    human 事件；补发保证 SDK 的 ``values.messages`` 含用户消息，前端
    human 计数增长后才会清理乐观消息（与 create 路径同因）。
    """
    try:
        messages, _ = await storage.list_messages(
            user_id,
            thread_id,
            limit=50,
        )
    except Exception:  # noqa: BLE001 —— join 回显尽力而为，失败不影响连接
        logger.exception(
            "deerflow: failed to load human chunks for thread %s",
            thread_id,
        )
        return []
    return [_msg_to_human_chunk(m) for m in messages if m.role == "user"]

deerflow_router = APIRouter(prefix="/api/threads", tags=["deerflow"])


# ── 请求模型 ──────────────────────────────────────────────────────────


class CreateRunRequest(BaseModel):
    """创建 run 的请求体。

    兼容 LangGraph SDK 的调用契约（前端经 ``useStream`` 发起）：

    - ``assistant_id`` 为 SDK 固定携带的别名（等价于原生 ``agent_id``）；
      两者都省略时回退 config.yaml 首个 seed agent。
    - ``input`` 接受 SDK 的 ``{"messages": [...]}`` / 单条消息 dict，
      转换后等价于原生 ``ChatRequest.input``。
    - ``session_id`` 可省略（缺省即 thread_id）；deer-flow 扩展参数
      （``stream_mode`` / ``multitask_strategy``）接受但忽略——本方案
      固定流模式与 reject 并发策略（裁剪项 1/2）。
    """

    agent_id: str | None = Field(
        default=None,
        description="Agent ID（与原生 /chat/ 一致）。与 assistant_id "
        "二选一；都省略时使用 config.yaml 首个 seed agent。",
    )
    assistant_id: str | None = Field(
        default=None,
        description="LangGraph SDK 别名（前端固定传 lead_agent）；"
        "与 agent_id 等价，二选一。",
    )
    session_id: str | None = Field(
        default=None,
        description="原生 session id。省略时即 thread_id（两者同一资源）。",
    )
    input: (
        Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | dict
        | None
    ) = Field(
        default=None,
        description="输入消息。兼容 LangGraph SDK 的 "
        "``{'messages': [...]}`` / 单条消息 dict，转换后等价于原生 "
        "ChatRequest.input。",
    )
    stream_mode: list[str] | str | None = Field(
        default=None,
        description="接受但忽略：本方案固定 messages + custom 流。",
    )
    multitask_strategy: str = Field(
        default="reject",
        description="接受但忽略：恒为 reject（409 语义由原生注册表保证）。",
    )
    on_disconnect: Literal["cancel", "continue"] = Field(
        default="cancel",
        description="客户端断线后行为：cancel 立即中断 run（默认，对齐 "
        "deer-flow）；continue 仅断开订阅、run 继续执行。",
    )
    custom_params: dict[str, Any] | None = Field(
        default=None,
        description="请求级自定义参数（空间码等），注入后台 run 任务，"
        "由工具中间件强制覆盖模型传参。",
    )


# ── 内部辅助 ──────────────────────────────────────────────────────────


def _resolve_session_id(thread_id: str, body: CreateRunRequest) -> str:
    """thread_id 与 session_id 同一资源；显式提供且不一致时报 400。"""
    if body.session_id is not None and body.session_id != thread_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"session_id={body.session_id!r} must equal thread_id "
                f"{thread_id!r} (thread_id == session_id)."
            ),
        )
    return thread_id


def _resolve_agent_id(body: CreateRunRequest) -> str:
    """agent_id / assistant_id / config 默认值，按序取第一个非空。"""
    if body.agent_id:
        return body.agent_id
    if body.assistant_id:
        return body.assistant_id
    default = _default_agent_id()
    logger.info("deerflow: agent_id omitted, defaulting to %r.", default)
    return default


def _fallback_agent_id(agent_id: str) -> str:
    """agent 未注册时回退默认 agent。

    LangGraph SDK 固定携带 ``assistant_id=lead_agent``，而注册表 =
    config.yaml seed agents（lifespan 已同步进原生 storage）；不存在的
    agent 回退到默认 agent，保证前端零改动可对话。
    """
    seed_ids = {entry.agent_id for entry in load_agents_from_yaml()}
    if agent_id not in seed_ids:
        fallback = _default_agent_id()
        logger.info(
            "deerflow: agent %r not registered, falling back to %r.",
            agent_id,
            fallback,
        )
        return fallback
    return agent_id


async def _resolve_chat_model_config(
    storage: StorageBase,
    request: Request,
    user_id: str,
    agent_id: str,
) -> ChatModelConfig | None:
    """把 config.yaml 的模型条目解析为原生 ChatModelConfig。

    场景绑定的 provider/model 为空时回退全局 active provider（bocomadp
    场景 model_provider 常留空）；匹配到条目后以用户维度的固定 id
    （``deerflow-<user_id>-<provider_id>``）把 api_key/base_url 幂等写入
    credential 存储，返回配置供原生 ``ChatService`` 构建模型。无可用
    条目时返回 None（由原生 404 报错兜底，不阻断请求）。
    """
    # 场景绑定的 provider/model 直接查 config.yaml 种子条目（lifespan
    # 已同步进 storage；框架 AgentData 无模型绑定字段，种子是唯一来源）
    provider_id = ""
    model_name = ""
    seed_entry = next(
        (
            candidate
            for candidate in load_agents_from_yaml()
            if candidate.agent_id == agent_id
        ),
        None,
    )
    if seed_entry is not None:
        provider_id = seed_entry.model_provider
        model_name = seed_entry.model_name
    if not provider_id:
        pm = getattr(request.app.state, "provider_manager", None)
        active = pm.get_active_model() if pm is not None else None
        if active is not None:
            provider_id = active.provider_id
            model_name = model_name or active.model_name

    entry = next(
        (
            candidate
            for candidate in load_models_from_yaml()
            if candidate.provider_id == provider_id
        ),
        None,
    )
    if entry is None or not entry.api_key:
        logger.warning(
            "deerflow: no model entry for provider %r; session created "
            "without chat_model_config.",
            provider_id,
        )
        return None

    credential_cls = CredentialFactory.get_credential_class(
        entry.provider_type,
    )
    # 与 bocomadp.config.build_model_instance 同构：简写匹配失败时补
    # _credential 后缀再试（CredentialFactory 只注册全称类）。
    if (
        credential_cls is None
        and not entry.provider_type.endswith("_credential")
    ):
        credential_cls = CredentialFactory.get_credential_class(
            f"{entry.provider_type}_credential",
        )
    if credential_cls is None:
        logger.warning(
            "deerflow: unknown provider_type %r; session created without "
            "chat_model_config.",
            entry.provider_type,
        )
        return None

    # SQL 存储的 credentials 表以全局 id 为主键：固定
    # ``deerflow-<provider_id>`` 会在第二个用户首次建会话时与既有行
    # 撞键（UniqueViolation），且 upsert_credential 的防越权保护会
    # 拒绝覆盖他人持有的 id。id 必须带 user_id 维度（Redis 后端本就
    # 按 user 命名空间隔离，此格式同样兼容）。
    credential_id = f"deerflow-{user_id}-{entry.provider_id}"
    credential_kwargs: dict[str, Any] = {
        "api_key": entry.api_key,
        "id": credential_id,
    }
    if entry.base_url and "base_url" in credential_cls.model_fields:
        credential_kwargs["base_url"] = entry.base_url
    await storage.upsert_credential(
        user_id,
        credential_cls(**credential_kwargs),
    )
    return ChatModelConfig(
        type=entry.provider_type,
        credential_id=credential_id,
        model=entry.model_name or entry.provider_id,
        parameters=entry.parameters,
    )


async def _ensure_agent(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
) -> None:
    """bocomadp 场景 agent 同步到原生 agent 存储（缺失时按 config.yaml 补写）。

    种子在 lifespan 以 ``user_id="default"`` 注册；原生 ChatService 经
    ``ResourceAccessService.resolve_agent`` 按调用者 user_id 解析 agent，
    这里把 config.yaml 的 seed agent 以原生记录形态按 user_id 补写，
    保证 deerflow 链路选中的 agent（含回退默认值）都能在原生
    ChatService 中解析到。已存在时不覆盖（保留用户后续的修改）。
    """
    if await storage.get_agent(user_id, agent_id) is not None:
        return
    entry = next(
        (
            candidate
            for candidate in load_agents_from_yaml()
            if candidate.agent_id == agent_id
        ),
        None,
    )
    if entry is None:
        logger.warning(
            "deerflow: agent %r not found in config.yaml; skipped sync.",
            agent_id,
        )
        return
    await storage.upsert_agent(
        user_id,
        AgentRecord(
            id=agent_id,
            user_id=user_id,
            data=AgentData(
                id=agent_id,
                name=entry.name or agent_id,
                system_prompt=entry.system_prompt,
                context_config=ContextConfig(),
                react_config=ReActConfig(max_iters=entry.max_iters),
            ),
        ),
    )
    logger.info(
        "deerflow: agent %r synced into native storage (user=%s).",
        agent_id,
        user_id,
    )


async def _ensure_session(
    storage: StorageBase,
    workspace_manager,
    request: Request,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> None:
    """原生 ChatService.run 要求 session 已存在且带模型配置；缺失时补齐。

    workspace_id 由 workspace_manager 的隔离策略分配（与原生 /session/
    创建路径一致）；chat_model_config 从 config.yaml 模型条目解析——
    bocomadp 场景 model_provider 常留空，此时回退全局 active provider，
    保证默认会话也能在原生链路上真实调用模型。
    """
    await _ensure_agent(storage, user_id, agent_id)
    model_config = await _resolve_chat_model_config(
        storage,
        request,
        user_id,
        agent_id,
    )
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is not None:
        if (
            model_config is not None
            and existing.config.chat_model_config is None
        ):
            await storage.upsert_session(
                user_id=user_id,
                agent_id=agent_id,
                config=existing.config.model_copy(
                    update={"chat_model_config": model_config},
                ),
                session_id=session_id,
            )
            logger.info(
                "deerflow: backfilled chat_model_config for session %s "
                "(agent=%s).",
                session_id,
                agent_id,
            )
        return
    workspace_id = workspace_manager.assign_workspace_id(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )
    config_kwargs: dict[str, Any] = {"workspace_id": workspace_id}
    if model_config is not None:
        config_kwargs["chat_model_config"] = model_config
    await storage.upsert_session(
        user_id=user_id,
        agent_id=agent_id,
        config=SessionConfig(**config_kwargs),
        session_id=session_id,
    )
    logger.info(
        "deerflow: session %s auto-created for agent %s (user=%s).",
        session_id,
        agent_id,
        user_id,
    )


async def _resolve_custom_params(
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    user_id: str,
    agent_id: str,
    session_id: str,
    requested: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析本次 run 的 custom_params（对齐 deer-flow ``_resolve_custom_params``）。

    请求携带 custom_params → 落盘到会话绑定的 workspace（后续请求可
    回退恢复）并直接采用；未携带 → 从会话 workspace 的落盘文件回退
    加载（HITL 确认续跑等场景空间码约束持续生效）。

    workspace 解析复用与 skill_router 相同的 DB 持久化路径
    （``session.config.workspace_id``），任意隔离策略下都精确；落盘 /
    读盘非致命——workspace 不可用或文件缺失时降级为空 dict，不阻断
    run 创建。
    """
    if requested is not None:
        # 运行时覆盖——落盘供后续请求恢复（对齐 deer-flow 保存语义）
        session_record = await storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session_record is not None:
            try:
                workspace = await workspace_manager.get_workspace(
                    user_id,
                    agent_id,
                    session_id,
                    session_record.config.workspace_id,
                )
                await save_custom_params_to_workspace(
                    workspace,
                    session_id,
                    requested,
                )
            except Exception:  # noqa: BLE001 —— 落盘失败不阻断 run 创建
                logger.warning(
                    "deerflow: workspace unavailable for session %s; "
                    "custom_params persist skipped",
                    session_id,
                    exc_info=True,
                )
        return requested

    # 无运行时值——尝试从会话 workspace 回退加载
    session_record = await storage.get_session(
        user_id,
        agent_id,
        session_id,
    )
    if session_record is None:
        return {}
    try:
        workspace = await workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session_record.config.workspace_id,
        )
        loaded = await load_custom_params_from_workspace(
            workspace,
            session_id,
        )
    except Exception:  # noqa: BLE001 —— 读盘失败降级为空 dict
        logger.warning(
            "deerflow: workspace unavailable for session %s; "
            "custom_params fallback skipped",
            session_id,
            exc_info=True,
        )
        return {}
    return loaded or {}


def _set_run_auth_contexts(
    params: dict[str, Any],
) -> dict[str, Token]:
    """spawn 前注入认证上下文：ResolvedAuth + guwp token 联动。

    对齐 deer-flow ``_resolve_auth_params``：把 custom_params 的认证
    字段解析为 :class:`ResolvedAuth` 写入 ContextVar，供 run 任务内的
    工具后端经 :func:`get_resolved_auth` 读取。

    ``guwp_token`` 同时联动 agent-factory 的 ``_current_token``
    ContextVar——run 任务内 ``_resolve_session_token``（main.py）读取
    它并持久化到 session token store，技能下载等工具直接可用。

    返回各 ContextVar 的 reset token，spawn 完成后逐项 reset
    （``asyncio.create_task`` 已复制上下文快照，reset 不影响后台任务）。
    """
    tokens: dict[str, Token] = {}
    tokens["auth"] = set_resolved_auth(resolve_auth_params(params))
    guwp_token = str(params.get("guwp_token") or "")
    if guwp_token:
        from bocomadp.tools.agent_factory_tools import _current_token

        tokens["guwp"] = _current_token.set(guwp_token)
    return tokens


def _reset_run_auth_contexts(tokens: dict[str, Token]) -> None:
    """恢复 :func:`_set_run_auth_contexts` 注入的 ContextVar。"""
    try:
        guwp_token = tokens.get("guwp")
        if guwp_token is not None:
            from bocomadp.tools.agent_factory_tools import _current_token

            _current_token.reset(guwp_token)
    except Exception:  # noqa: BLE001 —— reset 失败仅告警
        logger.warning(
            "deerflow: failed to reset guwp token context (non-fatal)",
            exc_info=True,
        )
    try:
        auth_token = tokens.get("auth")
        if auth_token is not None:
            reset_resolved_auth(auth_token)
    except Exception:  # noqa: BLE001 —— reset 失败仅告警
        logger.warning(
            "deerflow: failed to reset auth context (non-fatal)",
            exc_info=True,
        )


async def _build_user_confirm_event(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
    session_id: str,
    response: dict,
) -> UserConfirmResultEvent | None:
    """前端确认卡片应答 → UserConfirmResultEvent（Case B 续跑）。

    按 ``request_id == "confirm-{tool_call.id}"`` 匹配会话中 ASKING 状态
    的待确认工具调用；value 映射：

    - ``confirm`` → 同意（rules=None）
    - ``reject`` → 拒绝
    - ``confirm_always`` → 同意且把工具调用携带的 suggested_rules 一并
      传入（落入 allow_rules，后续同前缀命令免确认）

    匹配不到待确认工具调用（会话状态已变化/重放请求）时返回 None，
    由调用方回退为普通消息处理，不阻断。
    """
    request_id = str(response.get("request_id", ""))
    value = str(response.get("value", ""))
    session_record = await storage.get_session(
        user_id,
        agent_id,
        session_id,
    )
    if session_record is None:
        logger.warning(
            "deerflow: confirm response %r dropped, session %s not found",
            request_id,
            session_id,
        )
        return None
    agent_record = await storage.get_agent(user_id, agent_id)
    agent_name = (
        agent_record.data.name if agent_record is not None else agent_id
    )
    awaiting = session_record.state.get_awaiting_tool_calls(agent_name)
    tool_call = next(
        (tc for tc in awaiting if f"confirm-{tc.id}" == request_id),
        None,
    )
    if tool_call is None:
        logger.warning(
            "deerflow: confirm response %r matches no awaiting tool call "
            "in session %s",
            request_id,
            session_id,
        )
        return None
    confirmed = value != "reject"
    rules = (
        list(tool_call.suggested_rules)
        if value == "confirm_always" and tool_call.suggested_rules
        else None
    )
    return UserConfirmResultEvent(
        reply_id=session_record.state.reply_id,
        confirm_results=[
            ConfirmResult(
                confirmed=confirmed,
                tool_call=tool_call,
                rules=rules,
            ),
        ],
    )


def _spawn_run(
    run_manager: RunManager,
    chat_run_registry: ChatRunRegistry,
    chat_service: ChatService,
    user_id: str,
    session_id: str,
    agent_id: str,
    input_msg: Any,
) -> tuple[RunRecord, asyncio.Task]:
    """RunManager 记账 + 原生注册表 spawn；任何冲突 → 409。"""
    try:
        record = run_manager.create_or_reject(
            user_id,
            session_id,
            agent_id,
            native_registry=chat_run_registry,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    try:
        task = chat_run_registry.spawn(
            chat_service.run(
                user_id,
                session_id,
                agent_id,
                input_msg,
                run_id=record.run_id,
            ),
            session_id=session_id,
            name=f"deerflow-run:{record.run_id}",
        )
    except RuntimeError as e:
        run_manager.set_status(
            record.run_id,
            RunStatus.ERROR,
            error="session busy",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    run_manager.set_status(record.run_id, RunStatus.RUNNING)

    def _on_done(t: asyncio.Task) -> None:
        # 只在仍 RUNNING 时落终态：cancel 端点可能已置 interrupted，
        # 不覆盖；ChatService.run 内部吞异常，错误已由 REPLY_END(error)
        # 事件表达，此处仅兜底。
        rec = run_manager.get(record.run_id)
        if rec is None or rec.status != RunStatus.RUNNING:
            return
        if t.cancelled():
            run_manager.mark_finished(record.run_id, RunStatus.INTERRUPTED)
        elif t.exception() is not None:
            run_manager.mark_finished(
                record.run_id,
                RunStatus.ERROR,
                error=str(t.exception()),
            )
        else:
            run_manager.mark_finished(record.run_id, RunStatus.SUCCESS)

    task.add_done_callback(_on_done)
    return record, task


def _sse_generator(
    bridge: BusBridge,
    chat_service: ChatService,
    run_manager: RunManager,
    request: Request,
    user_id: str,
    session_id: str,
    agent_id: str,
    run_id: str,
    on_disconnect: str,
    run_finished: bool = False,
    human_chunks: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None]:
    """回放 + live 订阅 → deer-flow 帧；断线/异常/结束均收敛为帧。

    Args:
        run_finished (`bool`, optional):
            run 已确认结束（join 路径由调用方从 RunManager / 原生注册表
            推断）；为真时回放未遇 end 立即收尾，避免 live 空等挂死连接。
        human_chunks (`list[dict]`, optional):
            创建 run 时回显的用户输入 chunk（LangGraph human 消息，id 与
            storage 持久化一致）。SDK 依赖 messages 事件把用户消息并入
            ``values.messages``——缺失时前端 human 计数不增长，乐观消息
            永不清理，界面出现两条用户输入（"问题显示两次"）。
    """

    async def _gen() -> AsyncGenerator[str, None]:
        # HITL park 标志：收到确认请求帧（on_require_confirm）后置真。
        # park 是回复的正常终点（end 哨兵由 formatter 补发），此时
        # interrupt 会走"锁已释放"分支，enqueue UserInterruptEvent 把
        # ASKING 的待确认工具调用全部标记 interrupted——摧毁等待用户
        # 确认的状态，确认应答将永远匹配不到工具调用。故 finally 里
        # 仅断线（未 park）才 interrupt。
        hitl_parked = False
        try:
            # 首帧回显用户输入（先于一切总线事件，保证 values.messages
            # 顺序 [human, ai, ...]；id 与 storage 一致，刷新后去重不重复）
            for chunk in human_chunks or []:
                yield format_sse(
                    StreamEvent(
                        id="",
                        event=EVENT_MESSAGES,
                        data=[chunk, {"langgraph_node": "user"}],
                    ),
                )
            async for evt in bridge.subscribe_run(
                session_id,
                run_id,
                # 空串 Last-Event-ID 头视为无游标，避免 log_read 收到 '' 崩溃
                last_event_id=request.headers.get("Last-Event-ID") or None,
                run_finished=run_finished,
            ):
                if evt is END_SENTINEL:
                    # 状态同步落定（end 帧与 done 回调之间存在毫秒级窗口，
                    # 提前落定可避免紧随其后的新 run 误判 409）；error 帧
                    # 已先行落定 ERROR，此处不覆盖。
                    _finish_if_running(run_manager, run_id, RunStatus.SUCCESS)
                    yield format_sse(evt)
                    return
                if evt.event == EVENT_ERROR:
                    _finish_if_running(run_manager, run_id, RunStatus.ERROR)
                if (
                    evt.event == EVENT_CUSTOM
                    and isinstance(evt.data, dict)
                    and evt.data.get("type") == "on_require_confirm"
                ):
                    hitl_parked = True
                if await request.is_disconnected():
                    break
                yield format_sse(evt)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 —— 流内异常收敛为 error + end
            logger.exception(
                "deerflow stream failed for run %s: %s",
                run_id,
                e,
            )
            yield format_sse(
                StreamEvent(
                    id="",
                    event=EVENT_ERROR,
                    data={"message": str(e), "name": "StreamError"},
                ),
            )
            yield format_sse(END_SENTINEL)
        finally:
            if on_disconnect == "cancel" and not hitl_parked:
                try:
                    await chat_service.interrupt(
                        user_id,
                        session_id,
                        agent_id,
                    )
                except LookupError:
                    # run 已完成、session 已清理时的正常情形，不必告警
                    pass
                except Exception:  # noqa: BLE001 —— 兜底中断失败仅记日志
                    logger.exception(
                        "deerflow: interrupt on disconnect failed for "
                        "session %s run %s",
                        session_id,
                        run_id,
                    )

    return _gen()


def _finish_if_running(
    run_manager: RunManager,
    run_id: str,
    status: RunStatus,
) -> None:
    """run 仍活跃时落定终态；已结束（cancel/其他订阅者先行）不覆盖。"""
    rec = run_manager.get(run_id)
    if rec is not None and rec.active:
        run_manager.mark_finished(run_id, status)


def _streaming_response(
    thread_id: str,
    run_id: str,
    generator: AsyncGenerator[str, None],
) -> StreamingResponse:
    """组装 StreamingResponse：deer-flow 协议头 + Content-Location。"""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # LangGraph SDK 用正则从该头提取 run id（对齐 deer-flow）。
            "Content-Location": f"/api/threads/{thread_id}/runs/{run_id}",
        },
    )


# ── 端点 1：创建 run + 流式 ──────────────────────────────────────────


@deerflow_router.post(
    "/{thread_id}/runs/stream",
    summary="Create a run and stream events via SSE",
)
async def create_run_stream(
    thread_id: str,
    body: CreateRunRequest,
    request: Request,
    user_id: str = Depends(get_deerflow_user_id),
    run_manager: RunManager = Depends(get_run_manager),
    bridge: BusBridge = Depends(get_bridge),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> StreamingResponse:
    """创建 run（后台任务 = 原生 ChatService.run）并立即 SSE 流式。

    首个消费者自带回放语义：即使后台任务已开始发布事件，订阅建立前
    的事件仍会从 Redis Stream replay log 补齐（见 bridge 两阶段设计）。
    会话（thread）懒创建：thread_id 对应的 session 不存在时按 agent_id
    自动建库，保证原生 ChatService.run 的 session 前置条件成立。
    """
    session_id = _resolve_session_id(thread_id, body)
    agent_id = _fallback_agent_id(_resolve_agent_id(body))
    converted = _convert_input(body.input)
    await _ensure_session(
        storage,
        workspace_manager,
        request,
        user_id,
        agent_id,
        session_id,
    )
    if isinstance(converted, _HumanInputResponseMarker):
        # 前端确认卡片应答：构造 UserConfirmResultEvent 续跑（Case B）。
        # 原 human 消息仍回显 chunk——前端依赖 messages 事件中的 human
        # 消息让 human 计数增长、清理乐观消息（hide_from_ui 不可见）。
        confirm_event = await _build_user_confirm_event(
            storage,
            user_id,
            agent_id,
            session_id,
            converted.response,
        )
        human_input_msg = _langgraph_message_to_msg(converted.raw)
        if confirm_event is None:
            # 匹配不到待确认工具调用（状态已变化/重放请求）：按普通
            # 消息兜底处理，不阻断对话。
            input_msg = human_input_msg
        else:
            input_msg = confirm_event
        human_chunks = _collect_human_chunks(human_input_msg)
    else:
        input_msg = converted
        human_chunks = _collect_human_chunks(input_msg)
    # spawn 前注入请求级 custom_params：asyncio.create_task 复制当前
    # ContextVar 上下文到后台 run 任务，工具中间件在 run 任务内读取
    # 强制覆盖模型传参；spawn 后 reset 不影响已创建的子任务。
    resolved_params = await _resolve_custom_params(
        storage,
        workspace_manager,
        user_id,
        agent_id,
        session_id,
        body.custom_params,
    )
    ctx_token = set_custom_params(resolved_params)
    auth_tokens = _set_run_auth_contexts(resolved_params)
    try:
        record, _task = _spawn_run(
            run_manager,
            chat_run_registry,
            chat_service,
            user_id,
            session_id,
            agent_id,
            input_msg,
        )
    finally:
        _reset_run_auth_contexts(auth_tokens)
        reset_custom_params(ctx_token)
    logger.info(
        "deerflow: run %s created for thread %s (agent=%s).",
        record.run_id,
        thread_id,
        agent_id,
    )
    return _streaming_response(
        thread_id,
        record.run_id,
        _sse_generator(
            bridge,
            chat_service,
            run_manager,
            request,
            user_id,
            session_id,
            agent_id,
            record.run_id,
            body.on_disconnect,
            human_chunks=human_chunks,
        ),
    )


# ── 端点 2：创建 run + 阻塞等待 ──────────────────────────────────────


@deerflow_router.post(
    "/{thread_id}/runs/wait",
    summary="Create a run and block until it completes",
)
async def create_run_wait(
    thread_id: str,
    body: CreateRunRequest,
    request: Request,
    user_id: str = Depends(get_deerflow_user_id),
    run_manager: RunManager = Depends(get_run_manager),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> dict[str, Any]:
    """创建 run 并阻塞至后台任务完成，返回 run 终态。"""
    session_id = _resolve_session_id(thread_id, body)
    agent_id = _fallback_agent_id(_resolve_agent_id(body))
    input_msg = _convert_input(body.input)
    await _ensure_session(
        storage,
        workspace_manager,
        request,
        user_id,
        agent_id,
        session_id,
    )
    # 同 create_run_stream：spawn 前注入 custom_params（带请求值则先
    # 落盘、不带则从会话 workspace 回退），reset 不影响已创建的后台
    # run 任务（create_task 复制 ContextVar 上下文）。
    resolved_params = await _resolve_custom_params(
        storage,
        workspace_manager,
        user_id,
        agent_id,
        session_id,
        body.custom_params,
    )
    ctx_token = set_custom_params(resolved_params)
    auth_tokens = _set_run_auth_contexts(resolved_params)
    try:
        record, task = _spawn_run(
            run_manager,
            chat_run_registry,
            chat_service,
            user_id,
            session_id,
            agent_id,
            input_msg,
        )
    finally:
        _reset_run_auth_contexts(auth_tokens)
        reset_custom_params(ctx_token)
    try:
        await task
    except asyncio.CancelledError:
        pass
    rec = run_manager.get(record.run_id) or record
    return {
        "run_id": rec.run_id,
        "thread_id": thread_id,
        "status": rec.status.value,
        "error": rec.error,
    }


# ── 端点 3：join 已有 run ────────────────────────────────────────────


@deerflow_router.get(
    "/{thread_id}/runs/{run_id}/stream",
    summary="Join an existing run's stream (replay + live)",
)
async def join_run_stream(
    thread_id: str,
    run_id: str,
    request: Request,
    cancel_on_disconnect: bool = Query(default=False),
    user_id: str = Depends(get_deerflow_user_id),
    run_manager: RunManager = Depends(get_run_manager),
    bridge: BusBridge = Depends(get_bridge),
    chat_service: ChatService = Depends(get_chat_service),
    chat_run_registry: ChatRunRegistry = Depends(get_chat_run_registry),
    storage: StorageBase = Depends(get_storage),
) -> StreamingResponse:
    """订阅一个已有 run：先回放（``Last-Event-ID`` 断点续传）再 live。

    run 不在 RunManager 记账内时放行（兼容原生 ``/chat/`` 触发的 run
    ——其 run_id 可从 session 事件流的 ``run_id`` 字段获得）；session
    不匹配或不存在时 404。

    已确认结束的 run（记账落定终态，或原生注册表无活跃任务）回放后
    立即收尾——否则 live 阶段只剩心跳帧，连接永不关闭，前端
    ``isStreaming`` 卡死（“请等待当前响应完成”）。

    ``cancel_on_disconnect`` 对齐 SDK joinStream 的 query 参数（默认
    ``0``）：为真时断线取消 run；为假（默认）时断线仅断开订阅。
    """
    record = run_manager.get(run_id)
    if record is not None:
        if record.session_id != thread_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run {run_id} not found in thread {thread_id}.",
            )
        agent_id = record.agent_id
        existing = await storage.get_session(
            user_id,
            agent_id,
            thread_id,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread '{thread_id}' not found.",
            )
        # 记账已落定终态 → 无活跃事件流，回放后立即收尾
        run_finished = not record.active
    else:
        # 原生链路触发的 run：无记账记录，不校验（回放无事件则挂起等 live）。
        agent_id = ""
        # 原生注册表也无活跃任务 → run 必然已结束；仍进 live 只会空等
        # 心跳帧，SSE 连接永不关闭，故回放后立即收尾。
        native_task = chat_run_registry.get(thread_id)
        run_finished = native_task is None or native_task.done()
        logger.debug(
            "deerflow: joining unregistered run %s on thread %s "
            "(likely triggered via native /chat/); run_finished=%s.",
            run_id,
            thread_id,
            run_finished,
        )

    human_chunks = await _load_human_chunks(storage, user_id, thread_id)
    return _streaming_response(
        thread_id,
        run_id,
        _sse_generator(
            bridge,
            chat_service,
            run_manager,
            request,
            user_id,
            thread_id,
            agent_id,
            run_id,
            "cancel" if cancel_on_disconnect else "continue",
            run_finished=run_finished,
            human_chunks=human_chunks,
        ),
    )


# ── 端点 4：run 详情（SDK runs.get 终态预检）────────────────────────


@deerflow_router.get(
    "/{thread_id}/runs/{run_id}",
    summary="Get details of a run",
)
async def get_run_detail(
    thread_id: str,
    run_id: str,
    run_manager: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """返回 run 详情（对齐 LangGraph SDK ``runs.get`` 契约）。

    前端 SDK ``reconnectOnMount`` 时先调本端点做终态预检
    （``shouldSkipReconnect``）：run 已落定终态则跳过 joinStream 直接
    走 ``onSuccess``，避免在已结束的 run 上 join 空等心跳帧、
    ``isStreaming`` 永不翻转（“一直 thinking”）。

    未记账的 run（原生 ``/chat/`` 触发）返回 404——SDK 捕获后回退到
    joinStream，与真实 deer-flow 行为一致。
    """
    record = run_manager.get(run_id)
    if record is None or record.session_id != thread_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found in thread {thread_id}.",
        )
    return {
        "run_id": record.run_id,
        "thread_id": record.session_id,
        "assistant_id": record.agent_id,
        "status": record.status.value,
        "error": record.error,
        "metadata": {},
        "kwargs": {},
        "multitask_strategy": "reject",
        "created_at": "",
        "updated_at": "",
    }


# ── 端点 5：取消 ─────────────────────────────────────────────────────


@deerflow_router.post(
    "/{thread_id}/runs/{run_id}/cancel",
    summary="Cancel a running or pending run",
)
async def cancel_run(
    thread_id: str,
    run_id: str,
    request: Request,
    user_id: str = Depends(get_deerflow_user_id),
    run_manager: RunManager = Depends(get_run_manager),
    chat_service: ChatService = Depends(get_chat_service),
) -> dict[str, Any]:
    """取消 run：RunManager 状态置 interrupted + 原生 session 级 interrupt。

    安全性由原生分布式锁保证（一个 session 至多一个 run），run 级与
    session 级取消等价；join 方收到 ``REPLY_END(INTERRUPTED)`` 翻译的
    ``end`` 哨兵后立即断开。
    """
    record = run_manager.get(run_id)
    if record is None or record.session_id != thread_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found.",
        )
    if record.active:
        run_manager.mark_finished(run_id, RunStatus.INTERRUPTED)
    try:
        await chat_service.interrupt(
            user_id,
            thread_id,
            record.agent_id,
        )
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return {
        "run_id": run_id,
        "status": RunStatus.INTERRUPTED.value,
    }


__all__ = ["deerflow_router"]
