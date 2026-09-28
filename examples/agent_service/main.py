# -*- coding: utf-8 -*-
"""BocomADP — built on top of AgentScope's ``create_app``.

本示例在官方入口之上叠加企业内部扩展，同时也是所有关注点
统一装配的唯一入口（企业能力已全部整合进 ``bocomadp``）：

1. Load config (:mod:`bocomadp.config`).
2. Configure logging once at startup (:func:`configure_logging`).
3. Initialize the framework modules:
   - :class:`ToolRegistry`         — custom tools
   - :class:`MiddlewareRegistry`   — agent middlewares
   - :class:`RunManager`           — deerflow run bookkeeping
   - :class:`BusBridge`            — deerflow SSE bridge over MessageBus
4. Build the AgentScope app via :func:`create_app` (12 built-in routers).
5. Inject ASGI middlewares via ``extra_middlewares``.
6. Mount custom routers (deerflow SSE, health, stats).
7. Register sub-agent templates via ``custom_subagent_templates``.

企业扩展能力（bocomadp）：
   - 企业 agent 中间件（审计留痕）：``middleware/factory.py`` 主动 build 装配
   - 企业工具（HR / Doc / ITSM）：``tools/enterprise.py`` 主动 build 装配
   - ``platform_health_router``:  platform health check (``/platform/health``)

Run::

    cd agentscope/examples/agent_service
    python main.py
    # or
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope._logging import setup_logger
from agentscope.app import create_app, SubAgentTemplate
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus, RedisMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import AsyncSQLAlchemyStorage, RedisStorage
from agentscope.app.workspace_manager import (
    IsolationPolicy,
    LocalWorkspaceManager,
)
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.rag import QdrantStore

from bocomadp.agents.templates import load_subagent_templates
import bocomadp.memory as memory_module
from bocomadp.credential import ELLMCredential  # noqa: F401 — import 即注册自定义供应商
from bocomadp.config import (
    get_app_config,
    is_trace_correlation_enabled,
)
from bocomadp.concurrency.guard import ConcurrencyGuard
from bocomadp.logging.logging_config import configure_logging
from bocomadp.logging.trace_context import get_current_trace_id
from bocomadp.logging.trace_middleware import TraceMiddleware
from bocomadp.middleware.concurrency_guard import ConcurrencyGuardMiddleware
from bocomadp.middleware.active_skill import ActiveSkillMiddleware
from bocomadp.middleware.error_handler import ErrorHandlingMiddleware
from bocomadp.middleware.summarization import SummarizationMiddleware
from bocomadp.middleware.ellm_refresh import build_ellm_refresh_middleware
from agentscope.middleware import TracingMiddleware
from bocomadp.middleware.custom.event_log import EventLogMiddleware
from bocomadp.middleware.factory import build_enterprise_middlewares
from bocomadp.middleware.registry import MiddlewareRegistry
from bocomadp.middleware.tool_call_repair import ToolCallRepairMiddleware
from bocomadp.middleware.request_log import AccessLogMiddleware
from bocomadp.deerflow import BusBridge, RunManager
from bocomadp.deerflow.credentials import ensure_default_credentials
from bocomadp.deerflow.routers.auth_stub import auth_stub_router
from bocomadp.deerflow.routers.deerflow_chat import deerflow_router
from bocomadp.deerflow.routers.threads import threads_router
from bocomadp.routers.uploads import uploads_router
from bocomadp.routers.channels import channels_router
from bocomadp.routers.credential_model import credential_model_router
from bocomadp.routers.health import health_router
from bocomadp.routers.platform_health import platform_health_router
from bocomadp.routers.skill_router import skill_router
from bocomadp.routers.stats import stats_router
from bocomadp.routers.workspace_files import workspace_files_router
from bocomadp.routers.oss_download import oss_download_router
from bocomadp.routers.session_usage import session_usage_router
from bocomadp.routers.agent_tools import agent_tools_router
from bocomadp.routers.agent_tools import (
    load_tool_whitelists,
)
from bocomadp.routers.agent_concurrency import agent_concurrency_router
from bocomadp.routers.agent import agent_router
from bocomadp.agent_list_sort import patch_agent_list_sort
from bocomadp.open_agent_access import (
    patch_open_agent_access,
    patch_open_session_credentials,
    patch_open_runtime_credentials,
)
from bocomadp.team_access import patch_team_access
from bocomadp.team_briefing import patch_team_briefing
from bocomadp.projectors import WorkerFailureNotifier
from bocomadp.agent_session_purge import patch_agent_session_purge
from bocomadp.session_team_cascade import patch_session_team_cascade
from bocomadp.team_toolkit import patch_team_toolkit
from bocomadp.toolkit_whitelist import patch_get_toolkit
from bocomadp.tool_catalog import AGENT_CREATOR_ID
from bocomadp.deerflow.model_patch import patch_get_model
# 会话默认权限模式：给原生 POST /sessions/ 前插包裹路由（一处配置生效）
from bocomadp.session_default_mode import (
    install_create_session_default_mode,
)
# 框架内置 agent_router 只用于"摘除"（专家团能力由 bocomadp 版覆盖）
from agentscope.app._router._agent import (
    agent_router as _framework_agent_router,
)
# 框架内置路由（credential / knowledge_bases / agent / session / schedule /
# skill / mcp / hub / workspace / tts_model / model / chat）全部由 create_app()
# 统一注册，本文件无需 import 或 include；框架 chat_router(POST /chat/) 与
# deerflow_router(POST /bocomadp/v1/threads/...) 路径不同，互不冲突。
from bocomadp.mcp import McpRegistry
from bocomadp.skills import ExternalSkillHub
from bocomadp.skills.bocom_skill_hub import BocomSkillHub
from bocomadp.tools import ToolRegistry, build_enterprise_tools, init_factory_tools
from bocomadp.uploads.manager import cleanup_stale_upload_staging_files

# K8s 沙箱工作区（纯配置驱动，零框架侵入）
from bocomadp.workspace import (
    build_k8s_workspace_manager,
    is_k8s_enabled,
    WhitelistWorkspaceManager,
)

# 在 agentscope 子模块被 import 之前完成 setup_logger，
# 以便它们使用的 ``as`` logger 自动拥有文件 handler。
_LOG_DIR = os.getenv("AGENTSCOPE_LOG_DIR", "/app/logs")
_LOG_FILE = os.path.join(_LOG_DIR, "events.log")
# 每日滚动的历史文件保留天数（backupCount；0 = 永不自动清理）
_LOG_BACKUP_COUNT = int(os.getenv("AGENTSCOPE_LOG_BACKUP_COUNT", "30"))
os.makedirs(_LOG_DIR, exist_ok=True)
setup_logger("INFO")  # 只挂 StreamHandler；文件 handler 由下方共享实例接管


class _EventsFormatter(logging.Formatter):
    """单个滚动 handler 同时服务 ``as`` 与 ``uvicorn.access``，
    按 logger 名保持各自原有行格式；统一注入 ``trace_id`` 字段，
    使模型/工具事件日志可与 access log 按 trace 关联。"""

    _FORMATS = {
        "as": (
            "%(asctime)s | %(levelname)-7s | "
            "[trace_id=%(trace_id)s] "
            "%(module)s:%(funcName)s:%(lineno)s - %(message)s"
        ),
        "uvicorn.access": (
            "%(asctime)s | %(levelname)-7s | %(name)s "
            "[trace_id=%(trace_id)s] - %(message)s"
        ),
    }

    def __init__(self) -> None:
        super().__init__(self._FORMATS["as"])
        self._sub_formatters = {
            name: logging.Formatter(fmt) for name, fmt in self._FORMATS.items()
        }

    def format(self, record: logging.LogRecord) -> str:
        # 与 JsonTraceFormatter 同策略：record 缺失 trace_id 时补当前
        # 上下文值（"-" 表示未绑定/未启用），不依赖 filter 安装顺序。
        # ``as`` logger 自带独立 handler（propagate=False），
        # configure_logging 只增强 root，因此必须在此层注入。
        if not hasattr(record, "trace_id"):
            record.trace_id = get_current_trace_id() or "-"
        sub = self._sub_formatters.get(record.name)
        return sub.format(record) if sub is not None else super().format(record)


# 每日滚动文件 handler：当天写 events.log，次日零点第一条日志触发滚动，
# 旧文件重命名为 events.log.<前一天日期>（如 events.log.2026-08-13），
# 超出 _LOG_BACKUP_COUNT 天的历史文件自动删除。
# 注意：``as`` 与 ``uvicorn.access`` 必须共享同一个 handler 实例 ——
# 若各自持有独立实例，两个实例会在同一天各自执行 rename，
# 后执行者会覆盖先滚动出的旧文件（os.rename 静默覆盖），当天日志丢失。
_events_file_handler = TimedRotatingFileHandler(
    _LOG_FILE,
    when="midnight",
    interval=1,
    backupCount=_LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_events_file_handler.suffix = "%Y-%m-%d"
_events_file_handler.setFormatter(_EventsFormatter())
logging.getLogger("as").addHandler(_events_file_handler)

# 把 uvicorn 的 HTTP 访问日志（``uvicorn.access``）也并入同一个文件，
# 便于在一个文件中对照"客户端请求 → 后端处理 → 模型调用 → 工具调用"时间线。
logging.getLogger("uvicorn.access").addHandler(_events_file_handler)

default_mcps = [
    MCPClient(
        name="browser-use",
        mcp_config=StdioMCPConfig(
            command="npx",
            args=["@playwright/mcp@latest"],
        ),
        is_stateful=True,
    ),
]

# ---------------------------------------------------------------------------
# 1. 配置加载 + 日志初始化
# ---------------------------------------------------------------------------
config = get_app_config()
configure_logging(config)
logger = logging.getLogger("bocomadp.main")

# ---------------------------------------------------------------------------
# 2. 框架模块初始化
# ---------------------------------------------------------------------------
# 启动时清理上次异常遗留的 .part 临时文件（crash recovery）
try:
    _cleaned = cleanup_stale_upload_staging_files()
    if _cleaned:
        logger.info("cleaned %d stale upload staging file(s)", _cleaned)
except Exception:  # 上传未配置也不应阻断启动
    logger.warning("cleanup_stale_upload_staging_files failed", exc_info=True)
tool_registry = ToolRegistry()
if config.tools.enabled:
    tool_registry.load_builtin_tools()
    if config.tools.load_custom:
        tool_registry.load_custom_tools()
logger.info("tools loaded: %s", tool_registry.list_tool_names())

# agent 级中间件：load_builtin 扫描 agent_middleware.py 的模块级实例，
# load_custom 扫描 middleware/custom/ 下的模块级实例。
middleware_registry = MiddlewareRegistry()
if config.middlewares.enabled:
    middleware_registry.load_builtin()
    if config.middlewares.load_custom:
        middleware_registry.load_custom()

# MCP 注册表：load_builtin 扫描 builtin_mcps.py，load_custom 扫描 mcp/custom/。
mcp_registry = McpRegistry()
if config.mcp.enabled:
    mcp_registry.load_builtin()
    if config.mcp.load_custom:
        mcp_registry.load_custom()

# ── 内置智能体：智能体工厂（agent-creator） ──
# 专门用于对话式创建/修改智能体：除工厂工具（build_agent_tools
# 按 agent_id 运行时注入）外，它也可以像普通智能体一样配置可配置集合
# M 内的工具（含 workspace builtins）。
# 注意：实际注册在下方 storage 创建之后进行。
_AGENT_CREATOR_ID = AGENT_CREATOR_ID
_AGENT_CREATOR_SYSTEM_PROMPT = (
    "你是智能体工厂，通过对话帮助用户创建和修改智能体配置。\n"
    "\n## 工作流程\n"
    "1. 需求澄清：了解智能体的目标、使用者、所需能力与行为约束\n"
    "2. 方案设计：给出角色定义、system prompt 草案、工具与技能组合建议\n"
    "3. 用户确认：确认后再落地，不替用户做假设\n"
    "4. 完成告知：告知智能体 ID 与使用方式\n"
    "\n## 注意点\n"
    "- 具体有哪些能力可用、如何操作，见 agent-factory 技能文档\n"
    "- 工具与技能选择遵循最小权限原则，只给任务必需的能力\n"
    "- 修改已有智能体时先查看当前配置，保留用户确认过的核心逻辑\n"
    "- 以 _ 开头的系统内置智能体不可删除\n"
)

# agent 全部存于框架 StorageBase（config.yaml agents 种子机制已移除，
# 启动时不再灌入；agent 由用户通过原生接口创建或运行前自行入库）。

# ---------------------------------------------------------------------------
# 3. MCP 服务器 + Agent 工具工厂
# ---------------------------------------------------------------------------
# MCP 列表从 mcp_registry 获取（builtin + custom 自动扫描），
# 不再手写 build_default_mcps()。新增 MCP：在 mcp/builtin_mcps.py
# 或 mcp/custom/xxx.py 导出 MCPClient 实例即可，重启生效。
def build_default_mcps() -> list:
    """返回注册表中的 MCPClient 实例列表。"""
    return mcp_registry.list_mcps()


# 会话维度的 guwp token 存储：
# 同一会话内 userId / token 恒定，正常消息路径每轮把请求头里的 token
# 刷新写入 Redis（TTL 7 天，活跃会话每消息自动续期）；resume 路径
# （WakeupDispatcher 后台 spawn，无请求上下文）回读。
# 本地模式（InMemoryMessageBus）退化为进程内 dict。
_session_tokens: dict[str, str] = {}

_SESSION_TOKEN_TTL_SECS = 7 * 24 * 3600


def _redis_client():
    """Return the async Redis client, or None in local mode."""
    if isinstance(message_bus, RedisMessageBus):
        try:
            return message_bus.get_client()
        except Exception:
            return None
    return None


async def _resolve_session_token(session_id: str) -> str:
    """Resolve the guwp token for one chat run.

    Context value wins (fresh from the request header); it is also
    persisted keyed by ``session_id`` so the resume path can read it
    back. Empty context → return the last persisted value.
    """
    from bocomadp.tools.agent_factory_tools import _current_token

    token = _current_token.get()
    is_redis_mode = isinstance(message_bus, RedisMessageBus)
    client = _redis_client()
    key = f"bocomadp:guwp_token:{session_id}"
    try:
        if token:
            if client is not None:
                await client.set(key, token, ex=_SESSION_TOKEN_TTL_SECS)
            elif not is_redis_mode:
                _session_tokens[session_id] = token
            return token

        if client is not None:
            cached = await client.get(key)
            token = cached.decode("utf-8") if cached else ""
        elif not is_redis_mode:
            token = _session_tokens.get(session_id, "")
    except Exception:
        logger.exception("session token resolve failed for %s", session_id)
    return token


# 通用工具构建入口（AgentScope ``AgentToolFactory``）：
# 合并「ToolRegistry 自动扫描的内置/自定义工具」+「主动 build 的企业工具」，
# 同时为 agent-creator 注入工厂工具。
async def build_agent_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    # Set user_id context var so agent-factory tools know the caller.
    from bocomadp.tools.agent_factory_tools import (
        _current_user_id,
        _current_token,
        _current_session_id,
    )
    _current_user_id.set(user_id)
    _current_session_id.set(session_id)

    # Session-scoped token: fresh value from the request context wins
    # and refreshes the store; the resume path falls back to the store.
    _current_token.set(await _resolve_session_token(session_id))

    tools = tool_registry.list_tools()
    tools.extend(
        await build_enterprise_tools(user_id, agent_id, session_id),
    )

    # Inject factory tools for the built-in agent-creator
    if agent_id == _AGENT_CREATOR_ID:
        from bocomadp.tools.agent_factory_tools import (
            create_agent,
            update_agent,
            delete_agent,
            list_agents,
            get_agent,
            get_agent_tools,
            list_tools_for_agent,
            set_agent_tools,
            list_available_skills,
            enable_skill_for_agent,
        )
        tools.extend([
            create_agent,
            update_agent,
            delete_agent,
            list_agents,
            get_agent,
            get_agent_tools,
            list_tools_for_agent,
            set_agent_tools,
            list_available_skills,
            enable_skill_for_agent,
        ])

    # Apply the per-agent tool whitelist managed by agent_tools_router
    # (PUT/DELETE /agents/{id}/tools/{name}):
    #   empty  -> every tool above stays available
    #   non-empty -> only the listed tool names survive
    # This makes the tool config APIs effective at runtime. For the
    # agent-creator its whitelist covers M plus its factory tools
    # (see _register_builtin_agents), so it keeps both.
    #
    # usableTools 名单内的企业工具豁免此白名单（请求级优先，只作用于
    # 企业工具层）：名单换算为当前运行时形态的工具名并入 allowed，
    # builtins / 工厂工具等非企业工具不豁免。
    from bocomadp.deerflow.custom_params import get_custom_params
    from bocomadp.routers.agent_tools import _tool_whitelists
    from bocomadp.tools.enterprise import usable_enterprise_tool_names

    whitelist = _tool_whitelists.get(agent_id, [])
    if whitelist:
        allowed = set(whitelist) | usable_enterprise_tool_names(
            get_custom_params().get("usableTools"),
        )
        tools = [
            t for t in tools if getattr(t, "name", "") in allowed
        ]

    return tools


# 通用中间件构建入口（AgentScope ``AgentMiddlewareFactory``）：
# 合并「MiddlewareRegistry 自动扫描的内置中间件」+「主动 build 的企业中间件」；
# 经 ``_build_agent_middlewares_with_ellm`` 传给 create_app 的
# ``extra_agent_middlewares``，与注册表视图保持同源。
# 企业中间件采用主动 build（middleware/factory.py），
# 按会话创建独立实例，不依赖 custom/ 被动扫描。
async def build_agent_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[Any]:
    # 显式标注 list[Any]：中间件列表是异构的（registry 扫描 + 企业主动 build），
    # 下面的重排若让推导收窄成某个具体中间件类型，会污染下游 append/extend。
    middlewares: list[Any] = middleware_registry.list_middlewares()
    middlewares.extend(
        await build_enterprise_middlewares(
            user_id,
            agent_id,
            session_id,
        ),
    )
    # 需要在洋葱链最内层（真实 model() 调用之前）记录的中间件，使其在
    # MODEL_INPUT / Langfuse input 里拿到「最终送给模型」的消息（即
    # ToolResultPersistence / ToolResultBudget 替换后的 <persisted-output>
    # 预览），而不是替换前的原始结果。它们经 custom/ 自动扫描注册，位置由
    # 本函数决定：从通用列表剔除后统一追加到末尾（最内层）。
    inner = [
        m
        for m in middlewares
        if isinstance(m, (EventLogMiddleware, TracingMiddleware))
    ]
    if inner:
        middlewares = [
            m
            for m in middlewares
            if not isinstance(m, (EventLogMiddleware, TracingMiddleware))
        ]
        middlewares.extend(inner)
    # 体检/自愈（ToolCallRepairMiddleware）必须排在洋葱链**最外层**：
    # 它先保证 messages 里历史 tool_call 参数合法（修复/剔除），下游
    # （含最内层的 event_log / Langfuse）记录的才是真正发出去的内容。
    # 用稳定排序原地重排（而非重建列表），避免收窄 middlewares 的推导类型。
    middlewares.sort(
        key=lambda m: (
            0 if isinstance(m, ToolCallRepairMiddleware) else 1
        ),
    )
    return middlewares


# ---------------------------------------------------------------------------
# 4. 存储 / 消息总线 / 工作区 / 知识库
# ---------------------------------------------------------------------------
storage = AsyncSQLAlchemyStorage(
    url=config.db.url,
    create_tables=config.db.create_tables,
    # 连接池健康参数：pre_ping 探测陈旧连接自动重建，recycle 早于
    # 防火墙/NAT 空闲超时回收，避免 asyncpg connection is closed
    engine_kwargs={
        "pool_pre_ping": config.db.pool_pre_ping,
        "pool_recycle": config.db.pool_recycle,
    },
)


class _BuiltinAgentStorageProxy:
    """Storage proxy: fall back to user_id="default" for built-in agents.

    The built-in agent-creator is registered under ``user_id="default"``.
    Framework lookup paths (``ResourceAccessService.resolve_agent`` etc.)
    only query the caller's own user id, so without this proxy the
    built-in agent is invisible to every non-default user (404 on
    sessions/chat/agent views). This proxy extends the same fallback
    to the framework HTTP API paths.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @staticmethod
    def _shared_agent(user_id: str, agent_id: str) -> bool:
        """Built-in factory agent is shared: non-default users may
        access the ``default`` user's sessions for it (the web UI
        creates those sessions while logged out, then the user logs
        in and gets 404 'session not found' — see get_session)."""
        return user_id != "default" and agent_id == _AGENT_CREATOR_ID

    async def get_agent(self, user_id: str, agent_id: str) -> Any:
        record = await self._inner.get_agent(user_id, agent_id)
        if record is not None or user_id == "default":
            return record
        return await self._inner.get_agent("default", agent_id)

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> Any:
        """Owner-scoped lookup with a fallback to the shared
        ``default`` sessions of the built-in factory agent.

        The frontend can create agent-creator sessions before the
        user logs in (``X-User-ID: default``); once logged in as a
        real user those sessions 404 on every read (messages / mcp /
        skill / chat) and the UI shows 'session preparation failed'.
        """
        record = await self._inner.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if record is not None or not self._shared_agent(user_id, agent_id):
            return record
        return await self._inner.get_session(
            "default",
            agent_id,
            session_id,
        )

    async def list_sessions(self, user_id: str, agent_id: str) -> list:
        """Merge the shared ``default`` sessions for the factory agent
        so they keep showing in the caller's session list."""
        sessions = await self._inner.list_sessions(user_id, agent_id)
        if not self._shared_agent(user_id, agent_id):
            return sessions
        shared = await self._inner.list_sessions("default", agent_id)
        seen = {s.id for s in sessions}
        return sessions + [s for s in shared if s.id not in seen]

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete the caller's session; fall back to the shared
        ``default`` session when the caller only has the fallback."""
        ok = await self._inner.delete_session(user_id, agent_id, session_id)
        if ok or not self._shared_agent(user_id, agent_id):
            return ok
        return await self._inner.delete_session("default", agent_id, session_id)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete via framework storage, then clean up side records:
        per-agent tool whitelist + session memory config / Redis state.

        The framework's ``DELETE /agent/{id}`` (and team cascades)
        all funnel through this storage call; the bocomadp-only
        ``/agents`` router is unused by the product.

        Memory cleanup (best-effort, never blocks the delete result):
        - DB ``agent_memory_configs`` row: ``memory_store.memory_delete``;
        - Redis per-session state (``active_sessions`` member / ``turns`` /
          extract lock): scanned from ``active_sessions`` by agent dimension
          inside ``cleanup_agent_memory`` — no pre-delete session snapshot
          needed (the compound key embeds user_id, so cleanup resolves it
          from the active set instead).
          Platform-side delete remains a placeholder (local delete only).
        """
        ok = await self._inner.delete_agent(user_id, agent_id)
        if ok:
            try:
                from bocomadp.routers.agent_tools import (
                    _persist_whitelists,
                    _tool_whitelists,
                )

                if _tool_whitelists.pop(agent_id, None) is not None:
                    _persist_whitelists()
            except Exception:  # 白名单清理失败不影响删除结果
                logger.warning(
                    "failed to drop tool whitelist for %s",
                    agent_id,
                    exc_info=True,
                )
            try:
                # 记忆清理收口到 memory 包（DB 配置行 + Redis 会话态）；
                # 平台侧删除仍为占位（仅本地清理）。
                await memory_module.cleanup_agent_memory(agent_id)
            except Exception:  # 记忆清理失败不影响删除结果
                logger.warning(
                    "failed to drop memory records for agent %s",
                    agent_id,
                    exc_info=True,
                )
        return ok

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Any,
    ) -> None:
        """消息落库前写入"上下文窗口占用"（``metadata.context_usage``）。

        框架 ``Msg.append_event`` 把**一个 reply 内所有** ``MODEL_CALL_END``
        的 usage 累加进 ``msg.usage``（官方设计：本轮总消耗），因此多轮
        ReAct（工具调用循环）后 ``usage.input_tokens`` 已是 N 次请求之和，
        不再等于上下文窗口大小。真正的窗口值由
        :class:`~bocomadp.middleware.custom.context_usage.
        ContextUsageMiddleware` 按 ``reply_id`` 逐次覆盖记录，此处用
        ``msg.id``（== reply_id）取走并写进 metadata：

        - ``context_usage.input_tokens`` —— 最后一次模型调用的 prompt
          长度，即**当时上下文窗口的占用**；
        - ``context_usage.output_tokens`` —— 最后一次调用的输出长度；
        - ``context_usage.calls`` —— 该 reply 内的模型调用轮数（便于
          分辨单轮回复与工具循环）。

        ``msg.usage`` 原样保留（累加口径），两者并存互不影响。取不到记录
        时（用户消息、失败上报消息，或中间件未覆盖的调用）原样透传。
        """
        from bocomadp.middleware.custom.context_usage import (
            attach_context_usage,
        )

        attach_context_usage(msg)
        await self._inner.upsert_message(user_id, session_id, msg)

    async def __aenter__(self) -> "_BuiltinAgentStorageProxy":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


storage = _BuiltinAgentStorageProxy(storage)

# ── 初始化工厂工具（注入 ToolRegistry / McpRegistry）──
init_factory_tools(tool_registry, mcp_registry)

logger.info(
    "framework modules initialized: "
    "tools=%d middlewares=%d mcps=%d",
    len(tool_registry.list_tools()),
    len(middleware_registry.list_middlewares()),
    len(mcp_registry.list_mcps()),
)

vector_store = QdrantStore(location=":memory:")

# ── K8s 沙箱 vs 本地工作区 ──
# 生产环境使用 K8s 沙箱（ADP_K8S_ENABLED=true，默认），
# 本地开发可设置 ADP_K8S_ENABLED=false 退回到 LocalWorkspaceManager。
if is_k8s_enabled():
    # -- K8s 沙箱模式 —— 每个智能体的代码执行在独立的 K8s Pod 中运行。
    # -- 共享 PVC 模式下 skills/.mcp 存储在 agent 级 PVC，session 数据子目录隔离。
    from bocomadp.workspace.k8s_exec_patch import apply_k8s_exec_patch

    # k3s 的 apiserver 在 exec 进程退出后不发 WebSocket close 帧，
    # 框架写路径（stdin 通道）会永久等待挂起；必须在任何沙箱
    # 写操作发生之前应用 patch。
    apply_k8s_exec_patch()
    from agentscope.app.message_bus import RedisMessageBus

    workspace_manager = build_k8s_workspace_manager()
    # 与 AppConfig 单源一致：Redis 连接统一走 config.redis，避免裸环境变量前缀坑
    message_bus = RedisMessageBus(
        host=config.redis.host,
        port=config.redis.port,
        max_connections=config.redis.max_connections,
    )
else:
    # -- 本地模式 —— 工作区直接使用宿主机文件系统（开发/测试用）
    message_bus = InMemoryMessageBus()
    workspace_manager = LocalWorkspaceManager(
        basedir=str(config.workspace_dir),
        isolation=IsolationPolicy.PER_SESSION,
        default_mcps=build_default_mcps(),
    )

# 并发控制仅在生产 Redis 模式生效(InMemory 本地/测试模式零日志噪声)
from agentscope.app.message_bus import RedisMessageBus as _RedisMessageBus

_concurrency_active = isinstance(message_bus, _RedisMessageBus)
# 包装工作区管理器：框架把 MCP 从 workspace.list_mcps() 直接注入
# （不经过 extra_agent_tools），因此只能在 get_workspace 这一层按
# per-agent 白名单过滤（PUT/DELETE /agents/{id}/tools/{name}）。
workspace_manager = WhitelistWorkspaceManager(workspace_manager)

# ---------------------------------------------------------------------------
# 4.5 /chat 并发控制:Redis 原子占位 + 注册表 + 入口对账
# ---------------------------------------------------------------------------
# Redis 客户端惰性获取:连接池由框架 lifespan 进入 message_bus 时创建,
# get_client() 在进入前不可用,中间件运行期调用,失败即 fail-open。
def _get_redis_client():
    return message_bus.get_client()

concurrency_guard = ConcurrencyGuard(
    _get_redis_client,
    max_running=config.run_concurrency.max_running,
    max_running_per_user=config.run_concurrency.max_running_per_user,
)

# ---------------------------------------------------------------------------
# 5. 构建 App —— create_app 自动注册 12 个内置路由
# ---------------------------------------------------------------------------
trace_enabled = is_trace_correlation_enabled(config)


class TokenCaptureMiddleware:
    """Capture the ``guwpToken`` / ``X-User-ID`` request headers into
    ContextVars.

    Pure ASGI middleware: the ContextVars are set in the request task
    itself, so the framework's ``ChatRunRegistry.spawn`` (which uses
    ``asyncio.create_task``) copies them into the chat-run background
    task — the token stays available to agent-factory tools, and the
    user id feeds the agent event logs (``user_id=`` field) without
    touching framework internals.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            token = ""
            user_id = ""
            for key, value in scope.get("headers") or []:
                if key.lower() == b"guwptoken":
                    token = value.decode("utf-8", errors="replace")
                elif key.lower() == b"x-user-id":
                    user_id = value.decode("utf-8", errors="replace")
            from bocomadp.logging.trace_context import set_current_user_id
            from bocomadp.tools.agent_factory_tools import _current_token

            _current_token.set(token)
            if user_id:
                set_current_user_id(user_id)
        await self.app(scope, receive, send)


def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    """构建 ASGI 中间件栈(先注册者最外层)。"""
    return [
        # 最内层：捕获 guwpToken 到 ContextVar，随请求上下文透传给
        # 框架 chat-run 后台任务（agent-creator 工厂工具使用）。
        Middleware(TokenCaptureMiddleware),
        # 解析用户消息中的 /skill_name 前缀（存入 ContextVar，供提示词注入）
        Middleware(ActiveSkillMiddleware),
        Middleware(TraceMiddleware, enabled=trace_enabled),
        Middleware(AccessLogMiddleware, skip_paths=("/healthz", "/readyz")),
        Middleware(ErrorHandlingMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(
            ConcurrencyGuardMiddleware,
            guard=concurrency_guard,
            grace_secs=config.run_concurrency.grace_secs,
            enabled=config.run_concurrency.enabled
            and (
                config.run_concurrency.max_running > 0
                or config.run_concurrency.max_running_per_user > 0
            )
            and _concurrency_active,
        ),
    ]


# 通用中间件构建入口：registry 自动扫描 + 企业中间件主动 build
# + ELLM key 刷新中间件（每次模型调用前惰性刷新 apikey）。
# refresh_ahead_secs 来自 config.ellm_key_refresh，提前刷新留缓冲。
_ellm_refresh_mw_factory = build_ellm_refresh_middleware(
    storage,
    message_bus,
    refresh_ahead_secs=config.ellm_key_refresh.refresh_ahead_secs,
)

# 上下文压缩统一模型中间件：配置真源在 PG runtime_configs 表（summarization key，
# 可经 /api/config/summarization 热更新）；无记录视为未启用，压缩用会话自身模型。
_summarization_mw = SummarizationMiddleware(
    storage,
    message_bus,
)

# 图片解析统一多模态模型：与压缩模型同模式（PG runtime_configs 表 view_image key，
# 可经 /api/config/view_image 热更新，config.yaml 不再配置视觉模型）；注入工具
# 运行时依赖供查凭证/刷新 ELLM key，无记录/未启用时工具提示未配置。
from bocomadp.tools.builtin_tools import set_tool_runtime_deps

set_tool_runtime_deps(storage, message_bus)

# 会话记忆运行时依赖：redis client（供心跳/计数/锁）与 storage（取消息）。
# InMemory 本地模式 _get_redis_client() 返回 None → 记忆中间件仅检索注入，
# 计数/提取降级关闭；Redis 模式全量生效。
memory_module.configure_memory_runtime(
    redis_client_fn=_get_redis_client,
    storage=storage,
)


async def _build_agent_middlewares_with_ellm(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    mws = await build_agent_middlewares(user_id, agent_id, session_id)
    mws.extend(await _ellm_refresh_mw_factory(user_id, agent_id, session_id))
    mws.append(_summarization_mw)
    # 会话记忆中间件：memory_enabled 才装配（检索注入 + 轮数/静默触发）
    mws.extend(
        await memory_module.build_memory_middlewares(
            user_id,
            agent_id,
            session_id,
        ),
    )
    return mws


app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[
        ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN")),
        ExternalSkillHub(),
        BocomSkillHub(hub_id="bocom"),
    ],
    custom_subagent_templates=load_subagent_templates(),
    # 通用中间件构建入口：registry 自动扫描 + 企业中间件主动 build（审计留痕）
    # + ELLM key 刷新中间件（每次模型调用前惰性刷新 apikey）。
    extra_agent_middlewares=_build_agent_middlewares_with_ellm,
    # 通用工具构建入口：registry 自动扫描 + 企业工具主动 build（HR / Doc / ITSM）
    extra_agent_tools=build_agent_tools,
    title="BocomADP",
    extra_middlewares=build_asgi_middlewares(trace_enabled),
)


def _configurable_tool_names() -> list[str]:
    """Return the configurable tool set M.

    M = workspace builtins + ToolRegistry tools + MCP servers + framework
    team/planning tools + enterprise tools. Mirrors
    :func:`bocomadp.routers.agent_tools._all_tool_names` so the built-in
    agent-creator's whitelist covers exactly what the tool config APIs
    manage.
    """
    from bocomadp.tool_catalog import (
        BUILTIN_TOOL_NAMES,
        FRAMEWORK_TOOLS_META,
    )
    from bocomadp.tools.enterprise_catalog import enterprise_tool_names

    names: set[str] = set(BUILTIN_TOOL_NAMES)
    names.update(meta["name"] for meta in FRAMEWORK_TOOLS_META)
    names.update(enterprise_tool_names())
    names.update(tool_registry.list_tool_names())
    for mcp in mcp_registry.list_mcps():
        name = getattr(mcp, "name", "") or ""
        if name:
            names.add(name)
    return sorted(names)


# ── 注册内置智能体：智能体工厂（agent-creator）到框架 StorageBase ──
# 使用 user_id="default" 创建；对话上下文构建中对所有用户
# fallback 查询 default 用户，确保每个用户都能与 agent-creator 对话。
# 注意：框架 create_app 用 lifespan 创建 app（FastAPI(lifespan=...)），
# @app.on_event("startup") 注册的处理器会被 Starlette 静默忽略，因此
# 包装框架的 lifespan 上下文：框架资源全部就绪后、开始服务前执行注册。
async def _register_builtin_agents() -> None:
    """Ensure the agent-creator exists in framework persistent storage."""
    from agentscope.app.storage import AgentData, AgentRecord
    from agentscope.agent import ContextConfig as _ContextConfig
    from agentscope.agent import ReActConfig as _ReActConfig
    from bocomadp.routers.agent_tools import _tool_whitelists

    existing = await storage.get_agent("default", _AGENT_CREATOR_ID)
    if existing is not None:
        logger.info(
            "agent-creator already in framework storage: %s",
            _AGENT_CREATOR_ID,
        )
    else:
        record = AgentRecord(
            id=_AGENT_CREATOR_ID,
            user_id="default",
            data=AgentData(
                name="智能体工厂",
                system_prompt=_AGENT_CREATOR_SYSTEM_PROMPT,
                context_config=_ContextConfig(),
                react_config=_ReActConfig(max_iters=30),
            ),
        )
        await storage.upsert_agent("default", record)
        logger.info(
            "built-in agent registered in framework storage: %s",
            _AGENT_CREATOR_ID,
        )

    # Init tool whitelist — the factory tools plus the configurable
    # set M (builtins / project / framework / enterprise), so the factory
    # agent can use any normal tool the tool config APIs manage.
    # Idempotent: re-applied on every startup (not just first
    # registration) because the in-memory store is lost on restart.
    factory_tools = [
        "create_agent",
        "update_agent",
        "delete_agent",
        "list_agents",
        "get_agent",
        "get_agent_tools",
        "list_tools_for_agent",
        "set_agent_tools",
        "list_available_skills",
        "enable_skill_for_agent",
    ]
    _tool_whitelists[_AGENT_CREATOR_ID] = sorted(
        set(factory_tools) | set(_configurable_tool_names()),
    )


_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_builtin_agents(app):
    async with _original_lifespan(app):
        # 恢复持久化的工具白名单（内存存储重启会丢）
        load_tool_whitelists()
        # 市场审批人白名单（JSON 文件持久化 + 接口管理，末位保护：
        # 名单不可清空）——文件缺失时用 SEED_REVIEWERS 种子名单首次
        # 播种，之后文件即唯一真相源。
        from bocomadp import market_reviewers

        market_reviewers.load_whitelist()
        # 专家团关系表（expert_team_relations）——团队档案从 AgentData
        # 内嵌字段（team_config / parent_agent_id）迁出后的新家。
        # 必须早于下面所有读团队档案的补丁挂载。
        from bocomadp import team_store

        await team_store.ensure_team_tables(storage)
        # 智能体市场扩展表（agent_market）——平台智能体的领域分类档案，
        # 与专家团关系表同一套 bocomadp 自建表模式（热度不落库，实时聚合）。
        from bocomadp import market_store

        await market_store.ensure_market_tables(storage)
        # 发布审批表（agent_market_review）——发布走审批流，approve 后
        # 才插 agent_market 名单行（一智能体一记录，upsert 覆盖）。
        from bocomadp import market_review_store

        await market_review_store.ensure_review_tables(storage)
        # 智能体模板名单表（agent_template）——"可复制模板"白名单：
        # 只有名单内且 enabled=true 的智能体允许被 POST /agent/{id}/copy。
        # 与市场表同一套自建表模式：启动幂等建表（create_all），无迁移。
        from bocomadp import agent_template_store

        await agent_template_store.ensure_agent_template_tables(storage)
        # 智能体扩展表（agents_extend）——一智能体一行，payload 存该
        # 智能体的扩展数据（当前装已安装技能清单）。存起来是为了让
        # "查已装技能"不必进沙箱（原先必须解析 workspace → 路由 Pod →
        # exec 列举，Pod 被回收/容器重启时该查询就失败）。
        # 与上表同一套自建表模式：启动幂等建表（create_all）+ 列级迁移。
        from bocomadp import agents_extend_store

        await agents_extend_store.ensure_agents_extend_tables(storage)
        # 池并发配置：PG 真源回填 Redis（Redis 重启/清空后 per-agent 配置不丢）
        try:
            from bocomadp.pool_config import sync_all_to_redis

            synced = await sync_all_to_redis()
            if synced:
                logger.info("pool concurrency configs synced to redis: %d", synced)
        except Exception as e:  # noqa: BLE001
            logger.warning("pool concurrency sync skipped: %s", e)
        await _register_builtin_agents()
        # 专家团工具注入（workflow 严格交接 + AgentInvite 池回填）——
        # 原实现改 src/_service/_toolkit.py 的 get_toolkit，现搬迁到
        # bocomadp/team_toolkit.py；必须先于 patch_get_toolkit 挂载，
        # 让白名单包装看到已注入的完整 Toolkit。
        patch_team_toolkit()
        # 内置模型条目作为 default 用户默认凭证幂等入库（deerflow
        # 模型名解析的默认参数单一来源；失败仅告警不阻断启动）
        await ensure_default_credentials(storage)
        # 模型库（model_registry）进程内快照预热：同步接口
        # （EllmChatModel.list_models / context_size / think_tag）据此读取；
        # 内部已吞异常，DB 不可用时保留空快照、不阻断启动。
        from bocomadp.routers.model_registry import load_snapshot

        await load_snapshot()
        # 框架 get_toolkit 全量注入 Task/Team/workspace/middleware 工具，
        # 在首次 chat run 前包一层，按每智能体白名单过滤所有工具来源。
        patch_get_toolkit()
        # 请求级模型参数（thinking_enabled/reasoning_effort）：包装框架
        # get_model，run_context 携带时合并进模型 Parameters。
        patch_get_model()
        # 资源列表团队成员过滤 + is_self_built 标记——原实现改
        # src/_service/_access.py 的 list_resource，现搬迁到
        # bocomadp/team_access.py；必须先于 patch_agent_list_sort 挂载
        # （排序包装会把 parent_agent_id 透传给本过滤层）。
        patch_team_access()
        # 资源列表按 updated_at 倒序（最近修改优先）——原实现直接改
        # src/_service/_access.py，现按约定搬迁到 bocomadp/agent_list_sort.py。
        patch_agent_list_sort()
        # 专家团 leader 删除级联（自建成员级联删、被邀成员摘除引用）——
        # 原实现改 src/_service/_session.py 的 delete_agent，现搬迁到
        # bocomadp/session_team_cascade.py。
        patch_session_team_cascade()
        # 智能体删除的全量使用痕迹清理（所有用户的 sessions/messages
        # 一并删，框架级联只删 owner 视角）——bocomadp/agent_session_purge.py。
        patch_agent_session_purge()
        # 开放智能体交互：任意用户可与任意智能体对话（除 team worker），
        # 创建/更新会话接口的 agent 归属与凭证归属校验一并放开；
        # 运行时凭证解析（chat/embedding/TTS）同样放开，密钥跨用户可用；
        # 必须早于 patch_team_briefing 挂载（两者都包装 resolve_agent，
        # open 兜底在里层才能让 briefing 包装看到兜底结果）。
        patch_open_agent_access()
        patch_open_session_credentials()
        patch_open_runtime_credentials()
        # 专家团 briefing（leader 的 system prompt 注入团队成员/交接序）
        # 原实现改 src/_service/_chat.py 的 _run_impl，现搬迁到
        # bocomadp/team_briefing.py，包装 ResourceAccessService.resolve_agent。
        patch_team_briefing()
        # WorkerFailureNotifier（worker 失败时提醒团队 leader）原实现位于
        # src/_service/_projectors/_worker_failure_notifier.py，按约定搬迁到
        # bocomadp/projectors/；ChatService 由框架 lifespan 构造，因此这里
        # 直接向已构造实例追加（幂等）。
        chat_service = app.state.chat_service
        if not any(
            getattr(p, "KIND", None) == WorkerFailureNotifier.KIND
            for p in chat_service._projectors
        ):
            chat_service._projectors.append(
                WorkerFailureNotifier(app.state.storage)
            )
        yield


app.router.lifespan_context = _lifespan_with_builtin_agents


# ---------------------------------------------------------------------------
# 6. 将框架模块挂载到 app.state，供路由层访问
# ---------------------------------------------------------------------------
app.state.tool_registry = tool_registry
app.state.mcp_registry = mcp_registry
app.state.middleware_registry = middleware_registry
# deerflow 路由单例：RunManager 记账（原生 ChatRunRegistry 为 lifespan
# 单例，请求时由路由层注入，复用其 409 语义含原生 /chat/ 占用感知）；
# BusBridge 封装同一 MessageBus。
app.state.run_manager = RunManager()
app.state.bus_bridge = BusBridge(message_bus)

# ---------------------------------------------------------------------------
# 7. 在 12 个内置路由之上挂载自定义路由
# ---------------------------------------------------------------------------
# 覆盖内置 /agent 路由：框架 agent_router 不含专家团 8 个 /team/* 端点
# 与 CRUD 专家团行为（已按约定搬到 bocomadp/routers/agent.py）。FastAPI
# 0.141+ 的 include_router 不复制路由对象，而是插入惰性的
# _IncludedRouter 包装（持有 original_router 引用），因此摘除必须按
# 引用身份判断；旧版 FastAPI 才按路径判断。bocomadp 其余路由均以
# /agents（复数）等其它前缀挂载，不会误伤。
_framework_agent_paths = {
    r.path
    for r in _framework_agent_router.routes
    if getattr(r, "path", "").startswith("/agent")
}


def _is_framework_agent_route(r: object) -> bool:
    original = getattr(r, "original_router", None)
    if original is not None:  # FastAPI 0.141+: _IncludedRouter 包装
        return original is _framework_agent_router
    return getattr(r, "path", "") in _framework_agent_paths  # 旧版复制


app.router.routes[:] = [
    r for r in app.router.routes if not _is_framework_agent_route(r)
]
app.include_router(agent_router)
# 智能体记忆字段包裹路由（必须后于 include bocomadp agent_router）：
# 按路径移除 bocomadp agent_router 中被覆盖的 /agent/ 4 条 CRUD 路由
# （/agent/{id}/team/*、/agent/schema/v2 等专家团端点路径不重叠，不受影响），
# 前插包裹路由；包裹 handler 内部调用 bocomadp.routers.agent 的端点函数，
# 因此 /agent/ CRUD = 专家团逻辑 + 记忆字段，两套能力共存（方向 A）。
# 会话记忆能力装配：/memory/config 路由 + lifespan 启动静默扫描器
# （create_app 之后、bocomadp agent_router 之后；lifespan 包装须在
# _lifespan_with_builtin_agents 赋值之后，见下方调用点顺序）
memory_module.install_memory(app)
# 会话默认权限模式（create_app 之后、/api 子应用挂载之前）：
# 原生 POST /sessions/ 与 bocomadp POST /sessions/create 统一取
# config.yaml 的 default_permission_mode（显式传入时以显式值为准），
# 见 bocomadp/session_default_mode.py。
install_create_session_default_mode(app)
# 已装技能查询（create_app 之后、/api 子应用挂载之前）：
# GET /workspace/skill 前插为"先查 agents_extend，无记录才回源沙箱并写回"，
# 使该查询在 Pod 不可用时也能命中缓存；见 bocomadp/routers/skill_router.py
# 的 install_skill_list_from_table。
from bocomadp.routers.skill_router import install_skill_list_from_table

install_skill_list_from_table(app)
# 技能上传 / 卸载写穿（同样在 /api 挂载之前）：
# 框架的 POST /workspace/skill/upload 与 DELETE /workspace/skill/{name} 只动
# 沙箱，这里前插同路径包装，成功后再更新 agents_extend（used 标记的数据源）；
# 状态码 / 响应体 / 异常与框架实现逐字一致，见 bocomadp/skill_write_through.py。
from bocomadp.skill_write_through import install_skill_write_through

install_skill_write_through(app)
app.include_router(health_router)
app.include_router(stats_router)
app.include_router(session_usage_router)
app.include_router(agent_tools_router)
app.include_router(deerflow_router)
# deer-flow 前端认证桩（/api/deerflow/v1/auth/me、/api/deerflow/v1/auth/setup-status 固定用户）
app.include_router(auth_stub_router)
# deer-flow 渠道兼容占位路由（providers/connections 恒空，前端优雅降级）
app.include_router(channels_router)
# deerflow threads 管理端点（create/search/state/history，对话闭环最小集）
app.include_router(threads_router)
app.include_router(uploads_router)
app.include_router(platform_health_router)
# 外部 skill hub（目录查询 / 我的上传 / 下载安装）
app.include_router(skill_router)
# 智能体沙箱并发管理（写 PG 真源 + 同步 Redis）
app.include_router(agent_concurrency_router)
# 工作区文件列表 / 下载（/workspace/files、/workspace/files/download）
app.include_router(workspace_files_router)
# OSS 打包下载（/workspace/file-download）
app.include_router(oss_download_router)
# 按 (X-User-ID, credential_id) 查询凭证 payload（+ ELLM 凭证部分更新）
app.include_router(credential_model_router)
# 系统提示词管理（全局默认 + 按智能体自定义）
from bocomadp.routers.system_prompt import system_prompt_router
app.include_router(system_prompt_router)
# 运行时配置管理（PG runtime_configs 表，/config/{key} 通用 CRUD）
from bocomadp.routers.runtime_config import runtime_config_router
app.include_router(runtime_config_router)
# 模型库（PG model_registry 表：CRUD + ELLM 运行时模型候选唯一真源）
from bocomadp.routers.model_registry import model_registry_router
app.include_router(model_registry_router)
# 智能体凭证绑定（agent_credential 表：agent_id -> credential_id 的 CRUD）
from bocomadp.routers.agent_credential import agent_credential_router
app.include_router(agent_credential_router)
# 智能体市场（平台应用列表 / 精选推荐 / 默认标签打标，全开放）
from bocomadp.routers.market import market_router
app.include_router(market_router)
# 智能体模板名单（可复制白名单的 CRUD：/agent/template）
from bocomadp.routers.agent_template import agent_template_router
app.include_router(agent_template_router)


# ---------------------------------------------------------------------------
# 8. 统一 /api 前缀：把完整 app 挂载为子应用
# ---------------------------------------------------------------------------
# 所有路由统一挂到 /api 下，与 webui 前端契约一致（client.ts：后端自带
# /api 前缀，nginx / vite 代理均不剥前缀直接透传）：
#   内置 /chat、/agent...        → /api/chat、/api/agent...
#   bocomadp /agents、/files...  → /api/agents、/api/files...
#   threads 协议 /bocomadp/v1/threads → /api/bocomadp/v1/threads；deer-flow 前端桩
#   /deerflow/v1/auth → /api/deerflow/v1/auth（兼容保留）


@asynccontextmanager
async def _root_lifespan(root):
    # Starlette mount 不会自动传播子应用 lifespan，这里手动运行子应用
    # （框架资源生命周期 + 内置智能体注册均在 app 的 lifespan 中）。
    async with app.router.lifespan_context(app):
        yield


root_app = FastAPI(title="BocomADP", lifespan=_root_lifespan)
# 健康检查保留根层副本：K8s 探针直打 /healthz、/readyz，不受 /api 影响
root_app.include_router(health_router)
root_app.mount("/api", app)


if __name__ == "__main__":
    logger.info(
        "Starting BocomADP on %s:%s (trace_enhance=%s, format=%s, reload=%s)",
        config.service.host,
        config.service.port,
        trace_enabled,
        config.logging.enhance.format,
        config.service.reload,
    )
    # 传 app 对象而非 "main:root_app" 字符串：python main.py 时本模块先以
    # __main__ 执行一遍，若再让 uvicorn 按字符串 import "main"，顶层代码会
    # 被完整重跑第二遍（重复建 engine/装 lifespan、install_memory 等模块级
    # 日志全部打两遍）。传对象则不二次 import。
    # reload 热更需要 import 字符串，dev 可接受双跑；生产 reload=false 传对象。
    if config.service.reload:
        uvicorn.run(
            "main:root_app",
            host=config.service.host,
            port=config.service.port,
            reload=True,
        )
    else:
        uvicorn.run(
            root_app,
            host=config.service.host,
            port=config.service.port,
            reload=False,
        )
