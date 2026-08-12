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
   - :class:`HookRegistry`         — 8-phase lifecycle hooks
   - :class:`Runtime`              — 8-phase orchestrator
4. Build the AgentScope app via :func:`create_app` (12 built-in routers).
5. Inject ASGI middlewares via ``extra_middlewares``.
6. Mount custom routers (chat SSE, agent manage, models, health, stats).
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

import uvicorn
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
    load_models_from_yaml,
    build_model_instance,
)
from bocomadp.logging.logging_config import configure_logging
from bocomadp.logging.trace_middleware import TraceMiddleware
from bocomadp.middleware.error_handler import ErrorHandlingMiddleware
from bocomadp.middleware.ellm_refresh import build_ellm_refresh_middleware
from bocomadp.middleware.factory import build_enterprise_middlewares
from bocomadp.middleware.registry import MiddlewareRegistry
from bocomadp.middleware.request_log import AccessLogMiddleware
from bocomadp.providers import ProviderManager
from bocomadp.routers.agent_manage import (
    AgentConfigRequest,
    MultiAgentManager,
    agent_manage_router,
)
from bocomadp.routers.chat_sse import chat_sse_router
from bocomadp.routers.uploads import uploads_router
from bocomadp.routers.credential_model import credential_model_router
from bocomadp.routers.health import health_router
from bocomadp.routers.models import models_router
from bocomadp.routers.platform_health import platform_health_router
from bocomadp.routers.skill_router import skill_router
from bocomadp.routers.stats import stats_router
from bocomadp.routers.workspace_files import workspace_files_router
from bocomadp.routers.session_usage import session_usage_router
from bocomadp.routers.agent_tools import agent_tools_router
# 框架内置路由（credential / knowledge_bases / agent / session / schedule /
# skill / mcp / hub / workspace / tts_model / model / chat）全部由 create_app()
# 统一注册，本文件无需 import 或 include；框架 chat_router(POST /chat/) 与本项目
# chat_sse_router(POST /chat/run、/chat/stop) 路径不同，互不冲突。
from bocomadp.mcp import McpRegistry
from bocomadp.runtime import Runtime, HookRegistry
from bocomadp.skills import ExternalSkillHub
from bocomadp.tools import ToolRegistry, build_enterprise_tools, init_factory_tools
from bocomadp.uploads.manager import cleanup_stale_upload_staging_files

# K8s 沙箱工作区（纯配置驱动，零框架侵入）
from bocomadp.workspace import build_k8s_workspace_manager, is_k8s_enabled

# 在 agentscope 子模块被 import 之前完成 setup_logger，
# 以便它们使用的 ``as`` logger 自动拥有文件 handler。
_LOG_DIR = os.getenv("AGENTSCOPE_LOG_DIR", "/app/logs")
_LOG_FILE = os.path.join(_LOG_DIR, "events.log")
os.makedirs(_LOG_DIR, exist_ok=True)
setup_logger("INFO", filepath=_LOG_FILE)

# 把 uvicorn 的 HTTP 访问日志（``uvicorn.access``）也并入同一个文件，
# 便于在一个文件中对照"客户端请求 → 后端处理 → 模型调用 → 工具调用"时间线。
_access_logger = logging.getLogger("uvicorn.access")
_access_file_handler = logging.FileHandler(_LOG_FILE)
_access_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s - %(message)s",
    ),
)
_access_logger.addHandler(_access_file_handler)

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

hook_registry = HookRegistry()

multi_agent_manager = MultiAgentManager()

# ── 内置智能体：智能体工厂（agent-creator） ──
# 专门用于对话式创建/修改智能体，不需要 K8s 沙箱，
# 工具通过 AgentBuilder 在运行时按 agent_id 注入。
_AGENT_CREATOR_ID = "_agent-creator"
_AGENT_CREATOR_SYSTEM_PROMPT = (
    "你是智能体工厂，专门通过对话帮助用户创建和修改智能体配置。"
    "\n\n## 你的能力\n"
    "你可以：\n"
    "- 通过对话了解用户需求，设计合适的 system prompt 和工具组合\n"
    "- 调用 create_agent 创建新智能体\n"
    "- 调用 update_agent 修改已有智能体\n"
    "- 调用 delete_agent 删除智能体\n"
    "- 调用 list_agents 查看所有智能体\n"
    "- 调用 get_agent 查看指定智能体的完整配置\n"
    "- 调用 list_tools_for_agent 查看系统中可用的工具\n"
    "\n## 工作原则\n"
    "- 在创建智能体之前，先充分了解用户的需求和场景\n"
    "- 设计 system prompt 时遵循角色定义优先、行为边界明确的原则\n"
    "- 工具选择遵循最小权限原则：只选任务必需的\n"
    "- 你可以查看 Skill Viewer 获取更详细的智能体设计指引"
)
if not multi_agent_manager.get_agent(_AGENT_CREATOR_ID):
    multi_agent_manager.create_agent(
        AgentConfigRequest(
            agent_id=_AGENT_CREATOR_ID,
            name="智能体工厂",
            system_prompt=_AGENT_CREATOR_SYSTEM_PROMPT,
            enabled_tools=[
                "create_agent",
                "update_agent",
                "delete_agent",
                "list_agents",
                "get_agent",
                "list_tools_for_agent",
            ],
            max_iters=30,
            requires_sandbox=False,
        ),
    )
    logger.info("built-in agent registered: %s", _AGENT_CREATOR_ID)

# ── 初始化工厂工具（注入 MultiAgentManager / ToolRegistry / McpRegistry）──
init_factory_tools(multi_agent_manager, tool_registry, mcp_registry)

runtime = Runtime(
    hook_registry=hook_registry,
    tool_registry=tool_registry,
    middleware_registry=middleware_registry,
    provider_manager=provider_manager,
    multi_agent_manager=multi_agent_manager,
    heartbeat_interval=config.runtime.heartbeat_interval_seconds,
)

logger.info(
    "framework modules initialized: "
    "tools=%d middlewares=%d providers=%d agents=%d mcps=%d",
    len(tool_registry.list_tools()),
    len(middleware_registry.list_middlewares()),
    len(provider_manager.list_providers()),
    len(multi_agent_manager.list_agents()),
    len(mcp_registry.list_mcps()),
)

# ---------------------------------------------------------------------------
# 3. MCP 服务器 + Agent 工具工厂
# ---------------------------------------------------------------------------
# MCP 列表从 mcp_registry 获取（builtin + custom 自动扫描），
# 不再手写 build_default_mcps()。新增 MCP：在 mcp/builtin_mcps.py
# 或 mcp/custom/xxx.py 导出 MCPClient 实例即可，重启生效。
def build_default_mcps() -> list:
    """返回注册表中的 MCPClient 实例列表。"""
    return mcp_registry.list_mcps()


# 通用工具构建入口（AgentScope ``AgentToolFactory``）：
# 合并「ToolRegistry 自动扫描的内置/自定义工具」+「主动 build 的企业工具」，
# 同时供 Runtime 层的 ``AgentBuilder`` 注入使用（AgentBuilder 侧取 registry 部分）。
# 企业工具采用主动 build（tools/enterprise.py），不依赖 custom/ 被动扫描。
async def build_agent_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
):
    tools = tool_registry.list_tools()
    tools.extend(
        await build_enterprise_tools(user_id, agent_id, session_id),
    )
    return tools


# 通用中间件构建入口（AgentScope ``AgentMiddlewareFactory``）：
# 合并「MiddlewareRegistry 自动扫描的内置中间件」+「主动 build 的企业中间件」，
# 与 Runtime 层 AgentBuilder 注入的中间件视图保持一致。
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
)

vector_store = QdrantStore(location=":memory:")

# ── K8s 沙箱 vs 本地工作区 ──
# 生产环境使用 K8s 沙箱（ADP_K8S_ENABLED=true，默认），
# 本地开发可设置 ADP_K8S_ENABLED=false 退回到 LocalWorkspaceManager。
if is_k8s_enabled():
    # -- K8s 沙箱模式 —— 每个智能体的代码执行在独立的 K8s Pod 中运行。
    # -- 双 PVC 模式下 skills/.mcp 共享（agent PVC），session 数据隔离。
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

runtime.workspace_manager = workspace_manager

# ---------------------------------------------------------------------------
# 5. 构建 App —— create_app 自动注册 12 个内置路由
# ---------------------------------------------------------------------------
trace_enabled = is_trace_correlation_enabled(config)


def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    """构建 ASGI 中间件栈（由内到外）。"""
    return [
        Middleware(TraceMiddleware, enabled=trace_enabled),
        Middleware(AccessLogMiddleware, skip_paths=("/healthz", "/readyz")),
        Middleware(ErrorHandlingMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
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

# ---------------------------------------------------------------------------
# 6. 将框架模块挂载到 app.state，供路由层访问
# ---------------------------------------------------------------------------
app.state.runtime = runtime
app.state.provider_manager = provider_manager
app.state.multi_agent_manager = multi_agent_manager
app.state.tool_registry = tool_registry
app.state.mcp_registry = mcp_registry
app.state.middleware_registry = middleware_registry
app.state.hook_registry = hook_registry

# ---------------------------------------------------------------------------
# 7. 在 12 个内置路由之上挂载自定义路由
# ---------------------------------------------------------------------------
app.include_router(health_router)
app.include_router(stats_router)
app.include_router(session_usage_router)
app.include_router(agent_tools_router)
app.include_router(chat_sse_router)
app.include_router(uploads_router)
app.include_router(agent_manage_router)
app.include_router(models_router)
app.include_router(platform_health_router)
# 外部 skill hub（目录查询 / 我的上传 / 下载安装）
app.include_router(skill_router)
# 工作区文件列表 / 下载（/workspace/files、/workspace/files/download）
app.include_router(workspace_files_router)
# 按凭证查询模型（含单模型绑定过滤）
app.include_router(credential_model_router)


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
        "main:app",
        host=config.service.host,
        port=config.service.port,
        reload=config.service.reload,
    )
