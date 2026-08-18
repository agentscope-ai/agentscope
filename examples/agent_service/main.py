# -*- coding: utf-8 -*-
"""BocomADP — built on top of AgentScope's ``create_app``.

本示例在官方入口之上叠加企业内部扩展，同时也是所有关注点
统一装配的唯一入口（企业能力已全部整合进 ``bocomadp``）：

1. Load config (:mod:`bocomadp.config`).
2. Configure logging once at startup (:func:`configure_logging`).
3. Initialize the framework modules:
   - :class:`ToolRegistry`         — custom tools
   - :class:`MiddlewareRegistry`   — agent middlewares
   - :class:`ProviderManager`      — multi-model routing
   - :class:`RunManager`           — deerflow run bookkeeping
   - :class:`BusBridge`            — deerflow SSE bridge over MessageBus
4. Build the AgentScope app via :func:`create_app` (12 built-in routers).
5. Inject ASGI middlewares via ``extra_middlewares``.
6. Mount custom routers (deerflow SSE, models, health, stats).
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
from bocomadp.credential import ELLMCredential  # noqa: F401 — import 即注册自定义供应商
from bocomadp.config import (
    get_app_config,
    is_trace_correlation_enabled,
    load_agents_from_yaml,
    load_models_from_yaml,
    build_model_instance,
)
from bocomadp.concurrency.guard import ConcurrencyGuard
from bocomadp.logging.logging_config import configure_logging
from bocomadp.logging.trace_middleware import TraceMiddleware
from bocomadp.middleware.concurrency_guard import ConcurrencyGuardMiddleware
from bocomadp.middleware.error_handler import ErrorHandlingMiddleware
from bocomadp.middleware.ellm_refresh import build_ellm_refresh_middleware
from bocomadp.middleware.factory import build_enterprise_middlewares
from bocomadp.middleware.registry import MiddlewareRegistry
from bocomadp.middleware.request_log import AccessLogMiddleware
from bocomadp.providers import ProviderManager
from bocomadp.deerflow import BusBridge, RunManager
from bocomadp.deerflow.credentials import ensure_default_credentials
from bocomadp.deerflow.routers.auth_stub import auth_stub_router
from bocomadp.deerflow.routers.deerflow_chat import deerflow_router
from bocomadp.deerflow.routers.models import deerflow_models_router
from bocomadp.deerflow.routers.threads import threads_router
from bocomadp.routers.uploads import uploads_router
from bocomadp.routers.channels import channels_router
from bocomadp.routers.credential_model import credential_model_router
from bocomadp.routers.health import health_router
from bocomadp.routers.models import models_router
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
from bocomadp.toolkit_whitelist import patch_get_toolkit
# 框架内置路由（credential / knowledge_bases / agent / session / schedule /
# skill / mcp / hub / workspace / tts_model / model / chat）全部由 create_app()
# 统一注册，本文件无需 import 或 include；框架 chat_router(POST /chat/) 与
# deerflow_router(POST /deerflow/threads/...) 路径不同，互不冲突。
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
    按 logger 名保持各自原有行格式。"""

    _FORMATS = {
        "as": (
            "%(asctime)s | %(levelname)-7s | "
            "%(module)s:%(funcName)s:%(lineno)s - %(message)s"
        ),
        "uvicorn.access": (
            "%(asctime)s | %(levelname)-7s | %(name)s - %(message)s"
        ),
    }

    def __init__(self) -> None:
        super().__init__(self._FORMATS["as"])
        self._sub_formatters = {
            name: logging.Formatter(fmt) for name, fmt in self._FORMATS.items()
        }

    def format(self, record: logging.LogRecord) -> str:
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

provider_manager = ProviderManager()

# 从 config.yaml 加载模型配置并自动注册到 ProviderManager
if config.providers.enabled:
    _model_entries = load_models_from_yaml(config.providers.config_file)
    for _entry in _model_entries:
        try:
            _model = build_model_instance(_entry)
            provider_manager.register(
                provider_id=_entry.provider_id,
                model=_model,
                model_name=_entry.model_name or _entry.provider_id,
                display_name=_entry.display_name,
                supports_multimodal=_entry.supports_multimodal,
                metadata={"base_url": _entry.base_url} if _entry.base_url else {},
            )
            # 非首条或显式标记为活跃的，覆盖默认激活项
            if _entry.is_active:
                provider_manager.set_active(_entry.provider_id)
            logger.info(
                "provider registered from config.yaml: %s (model=%s)",
                _entry.provider_id,
                _entry.model_name or _entry.provider_id,
            )
        except Exception:
            logger.warning(
                "failed to register provider '%s' from config.yaml",
                _entry.provider_id,
                exc_info=True,
            )

# ── 内置智能体：智能体工厂（agent-creator） ──
# 专门用于对话式创建/修改智能体，不需要 K8s 沙箱，
# 工具通过 AgentBuilder 在运行时按 agent_id 注入。
# 注意：实际注册在下方 storage 创建之后进行。
_AGENT_CREATOR_ID = "_agent-creator"
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

# 场景种子（config.yaml 的 agents 段）不再灌入 MultiAgentManager ——
# chat / deerflow 按 agent_id 从框架 StorageBase 解析配置，因此种子注册
# 已迁移到 lifespan 的 ``_seed_agents_from_yaml``（storage 就绪后幂等
# upsert，与 ``_register_builtin_agents`` 同构）。

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
    if agent_id == "_agent-creator":
        from bocomadp.tools.agent_factory_tools import (
            create_agent,
            update_agent,
            delete_agent,
            list_agents,
            get_agent,
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
            list_tools_for_agent,
            set_agent_tools,
            list_available_skills,
            enable_skill_for_agent,
        ])

    # Apply the per-agent tool whitelist managed by agent_tools_router
    # (PUT/DELETE /agents/{id}/tools/{name}):
    #   empty  -> every tool above stays available
    #   non-empty -> only the listed tool names survive
    # This makes the tool config APIs effective at runtime (for agents
    # created by the agent-creator) and enforces least privilege for
    # the agent-creator itself (only its 9 factory tools remain).
    from bocomadp.routers.agent_tools import _tool_whitelists
    whitelist = _tool_whitelists.get(agent_id, [])
    if whitelist:
        allowed = set(whitelist)
        tools = [
            t for t in tools if getattr(t, "name", "") in allowed
        ]

    return tools


# 通用中间件构建入口（AgentScope ``AgentMiddlewareFactory``）：
# 合并「MiddlewareRegistry 自动扫描的内置中间件」+「主动 build 的企业中间件」；
# 经 ``_build_agent_middlewares_with_ellm`` 传给 create_app 的
# ``extra_agent_middlewares``，与注册表视图保持同源。
# 企业中间件（审计留痕）采用主动 build（middleware/factory.py），
# 按会话创建独立实例，不依赖 custom/ 被动扫描。
async def build_agent_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    middlewares = middleware_registry.list_middlewares()
    middlewares.extend(
        await build_enterprise_middlewares(user_id, agent_id, session_id),
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
        """Delete via framework storage, then drop the per-agent tool
        whitelist so the persisted whitelist file keeps no orphans.

        The framework's ``DELETE /agent/{id}`` (and team cascades)
        all funnel through this storage call; the bocomadp-only
        ``/agents`` router is unused by the product.
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
        return ok

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
    "tools=%d middlewares=%d providers=%d mcps=%d",
    len(tool_registry.list_tools()),
    len(middleware_registry.list_middlewares()),
    len(provider_manager.list_providers()),
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
    """Capture the ``guwpToken`` request header into a ContextVar.

    Pure ASGI middleware: the ContextVar is set in the request task
    itself, so the framework's ``ChatRunRegistry.spawn`` (which uses
    ``asyncio.create_task``) copies it into the chat-run background
    task, making the token available to agent-factory tools during
    the run.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            token = ""
            for key, value in scope.get("headers") or []:
                if key.lower() == b"guwptoken":
                    token = value.decode("utf-8", errors="replace")
                    break
            from bocomadp.tools.agent_factory_tools import _current_token
            _current_token.set(token)
        await self.app(scope, receive, send)


def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    """构建 ASGI 中间件栈(先注册者最外层)。"""
    return [
        # 最内层：捕获 guwpToken 到 ContextVar，随请求上下文透传给
        # 框架 chat-run 后台任务（agent-creator 工厂工具使用）。
        Middleware(TokenCaptureMiddleware),
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


# 通用中间件构建入口：registry 自动扫描 + 企业中间件主动 build（审计留痕）
# + ELLM key 刷新中间件（每次模型调用前惰性刷新 apikey）。
_ellm_refresh_mw_factory = build_ellm_refresh_middleware(storage, message_bus)


async def _build_agent_middlewares_with_ellm(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    mws = await build_agent_middlewares(user_id, agent_id, session_id)
    mws.extend(await _ellm_refresh_mw_factory(user_id, agent_id, session_id))
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

    # Init tool whitelist — only factory tools for agent-creator.
    # Idempotent: re-applied on every startup (not just first
    # registration) because the in-memory store is lost on restart.
    _tool_whitelists[_AGENT_CREATOR_ID] = [
        "create_agent",
        "update_agent",
        "delete_agent",
        "list_agents",
        "get_agent",
        "list_tools_for_agent",
        "set_agent_tools",
        "list_available_skills",
        "enable_skill_for_agent",
    ]


async def _seed_agents_from_yaml() -> None:
    """Seed scenario agents from config.yaml into framework storage.

    config.yaml 的 agents 段（场景种子）注册到框架 StorageBase 的
    ``user_id="default"`` 下（幂等：已存在则跳过）。chat / deerflow
    路由按 agent_id 从 storage 解析配置，因此种子场景对所有用户可见
    （``_build_context`` 与 ``_BuiltinAgentStorageProxy`` 均有 default
    用户 fallback）。

    框架 AgentData 没有模型绑定与技能白名单字段：
    - ``model_provider`` / ``model_name``：忽略，chat 沿用全局 active
      provider（ProviderManager）
    - ``enabled_tools``：仅在首次创建时写入工具白名单（与 storage
      记录同生命周期，已存在的 agent 不覆盖，避免重启清掉用户手动配置）
    - ``enabled_skills``：暂无技能白名单落地机制，忽略
    """
    from agentscope.app.storage import AgentData, AgentRecord
    from agentscope.agent import ContextConfig as _ContextConfig
    from agentscope.agent import ReActConfig as _ReActConfig
    from bocomadp.routers.agent_tools import (
        _persist_whitelists,
        _tool_whitelists,
    )

    whitelist_dirty = False
    for _entry in load_agents_from_yaml():
        existing = await storage.get_agent("default", _entry.agent_id)
        if existing is not None:
            continue
        record = AgentRecord(
            id=_entry.agent_id,
            user_id="default",
            data=AgentData(
                name=_entry.name or _entry.agent_id,
                system_prompt=_entry.system_prompt,
                context_config=_ContextConfig(),
                react_config=_ReActConfig(max_iters=_entry.max_iters),
            ),
        )
        await storage.upsert_agent("default", record)
        if _entry.enabled_tools:
            _tool_whitelists[_entry.agent_id] = list(_entry.enabled_tools)
            whitelist_dirty = True
        logger.info(
            "agent seeded from config.yaml into storage: %s",
            _entry.agent_id,
        )
    if whitelist_dirty:
        _persist_whitelists()


_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_builtin_agents(app):
    async with _original_lifespan(app):
        # 恢复持久化的工具白名单（内存存储重启会丢）
        load_tool_whitelists()
        # 池并发配置：PG 真源回填 Redis（Redis 重启/清空后 per-agent 配置不丢）
        try:
            from bocomadp.pool_config import sync_all_to_redis

            synced = await sync_all_to_redis()
            if synced:
                logger.info("pool concurrency configs synced to redis: %d", synced)
        except Exception as e:  # noqa: BLE001
            logger.warning("pool concurrency sync skipped: %s", e)
        await _register_builtin_agents()
        await _seed_agents_from_yaml()
        # config.yaml 模型条目作为 default 用户默认凭证入库（deerflow
        # 模型名解析的默认参数单一来源；幂等，失败仅告警不阻断启动）
        await ensure_default_credentials(storage)
        # 框架 get_toolkit 全量注入 Task/Team/workspace/middleware 工具，
        # 在首次 chat run 前包一层，按每智能体白名单过滤所有工具来源。
        patch_get_toolkit()
        yield


app.router.lifespan_context = _lifespan_with_builtin_agents


# ---------------------------------------------------------------------------
# 6. 将框架模块挂载到 app.state，供路由层访问
# ---------------------------------------------------------------------------
app.state.provider_manager = provider_manager
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
app.include_router(health_router)
app.include_router(stats_router)
app.include_router(session_usage_router)
app.include_router(agent_tools_router)
app.include_router(deerflow_router)
# deer-flow 模型列表（GET /api/deerflow/models，deer-flow Model 格式）
app.include_router(deerflow_models_router)
# deer-flow 前端认证桩（/api/deerflow/v1/auth/me、/api/deerflow/v1/auth/setup-status 固定用户）
app.include_router(auth_stub_router)
# deer-flow 渠道兼容占位路由（providers/connections 恒空，前端优雅降级）
app.include_router(channels_router)
# deerflow threads 管理端点（create/search/state/history，对话闭环最小集）
app.include_router(threads_router)
app.include_router(uploads_router)
app.include_router(models_router)
app.include_router(platform_health_router)
# 外部 skill hub（目录查询 / 我的上传 / 下载安装）
app.include_router(skill_router)
# 智能体沙箱并发管理（写 PG 真源 + 同步 Redis）
app.include_router(agent_concurrency_router)
# 工作区文件列表 / 下载（/workspace/files、/workspace/files/download）
app.include_router(workspace_files_router)
# OSS 打包下载（/workspace/file-download）
app.include_router(oss_download_router)
# 按凭证查询模型（含单模型绑定过滤）
app.include_router(credential_model_router)


# ---------------------------------------------------------------------------
# 8. 统一 /api 前缀：把完整 app 挂载为子应用
# ---------------------------------------------------------------------------
# 所有路由统一挂到 /api 下，与 webui 前端契约一致（client.ts：后端自带
# /api 前缀，nginx / vite 代理均不剥前缀直接透传）：
#   内置 /chat、/agent...        → /api/chat、/api/agent...
#   bocomadp /agents、/files...  → /api/agents、/api/files...
#   deerflow /deerflow/threads、/deerflow/v1/auth → /api/deerflow/threads...


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
    uvicorn.run(
        "main:root_app",
        host=config.service.host,
        port=config.service.port,
        reload=config.service.reload,
    )
