# -*- coding: utf-8 -*-
"""BocomADP — built on top of AgentScope's ``create_app``.

This is the single place where every concern is wired together:

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

In addition, enterprise extension cases from ``bankcomm_adp`` are wired in
alongside the core skeleton (kept as runnable examples, not replacing core):
   - ``extra_agent_middlewares``: audit trail + data masking (DLP)
   - ``extra_agent_tools``:       enterprise internal tools (HR / Doc / ITSM)
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

from agentscope.app import create_app
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
from agentscope.rag import QdrantStore

from bocomadp.agents.templates import load_subagent_templates
from bocomadp.config import load_config, is_trace_correlation_enabled
from bocomadp.logging.logging_config import configure_logging
from bocomadp.logging.trace_middleware import TraceMiddleware
from bocomadp.middleware.agent_middleware import LoggingMiddleware
from bocomadp.middleware.error_handler import ErrorHandlingMiddleware
from bocomadp.middleware.registry import MiddlewareRegistry
from bocomadp.middleware.request_log import AccessLogMiddleware
from bocomadp.providers import ProviderManager
from bocomadp.routers.agent_manage import (
    MultiAgentManager,
    agent_manage_router,
)
from bocomadp.routers.chat_sse import chat_sse_router
from bocomadp.routers.health import health_router
from bocomadp.routers.models import models_router
from bocomadp.routers.stats import stats_router
from bocomadp.runtime import Runtime, HookRegistry
from bocomadp.tools import ToolRegistry

# 企业扩展（案例）：管控中间件 + 工具 + 自有路由
# health_router 重命名为 platform_health_router，避免与 bocomadp 的 health_router 同名
from bankcomm_adp.middlewares import build_enterprise_middlewares
from bankcomm_adp.routers import health_router as platform_health_router
from bankcomm_adp.tools import build_enterprise_tools

# ---------------------------------------------------------------------------
# 1. 配置加载 + 日志初始化
# ---------------------------------------------------------------------------
config = load_config()
configure_logging(config)
logger = logging.getLogger("bocomadp.main")

# ---------------------------------------------------------------------------
# 2. 框架模块初始化
# ---------------------------------------------------------------------------
tool_registry = ToolRegistry()
if config.tools.enabled:
    tool_registry.load_builtin_tools()
logger.info("tools loaded: %s", tool_registry.list_tool_names())

middleware_registry = MiddlewareRegistry()
middleware_registry.register(LoggingMiddleware())

provider_manager = ProviderManager()

hook_registry = HookRegistry()

multi_agent_manager = MultiAgentManager()

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
    "tools=%d middlewares=%d providers=%d agents=%d",
    len(tool_registry.list_tools()),
    len(middleware_registry.list_middlewares()),
    len(provider_manager.list_providers()),
    len(multi_agent_manager.list_agents()),
)

# ---------------------------------------------------------------------------
# 3. MCP 服务器 + Agent 工具工厂
# ---------------------------------------------------------------------------
def build_default_mcps() -> list[MCPClient]:
    """构建每个工作区默认挂载的 MCP 服务器列表。"""
    mcps = [
        MCPClient(
            name="browser-use",
            mcp_config=StdioMCPConfig(
                command="npx",
                args=["@playwright/mcp@latest"],
            ),
            is_stateful=True,
        ),
    ]
    if os.getenv("AMAP_API_KEY"):
        mcps.append(
            MCPClient(
                name="amap",
                mcp_config=HttpMCPConfig(
                    url=f"https://mcp.amap.com/mcp?key={os.environ['AMAP_API_KEY']}",
                ),
                is_stateful=False,
            ),
        )
    return mcps


# AgentScope ``AgentToolFactory`` —— 返回与内置 ``/chat`` 端点一致的自定义工具，
# 同时供 Runtime 层的 ``AgentBuilder`` 注入使用，保持两条 agent 创建路径的工具视图一致。
# 同时合并 ``bankcomm_adp`` 企业内部工具（HR / 文档库 / ITSM 占位）。
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


# ---------------------------------------------------------------------------
# 4. 存储 / 消息总线 / 工作区 / 知识库
# ---------------------------------------------------------------------------
storage = RedisStorage(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
)

vector_store = QdrantStore(location=":memory:")

workspace_manager = LocalWorkspaceManager(
    basedir=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "workspaces",
    ),
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


app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=workspace_manager,
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN"))],
    custom_subagent_templates=load_subagent_templates(),
    # 企业管控中间件（审计 + DLP），与核心中间件并存
    extra_agent_middlewares=build_enterprise_middlewares,
    # 已合并核心工具 + bankcomm_adp 企业工具
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
app.state.middleware_registry = middleware_registry
app.state.hook_registry = hook_registry

# ---------------------------------------------------------------------------
# 7. 在 12 个内置路由之上挂载自定义路由
# ---------------------------------------------------------------------------
app.include_router(health_router)
app.include_router(stats_router)
app.include_router(chat_sse_router)
app.include_router(agent_manage_router)
app.include_router(models_router)
# 企业扩展（案例）：/platform/health
app.include_router(platform_health_router)


if __name__ == "__main__":
    logger.info(
        "Starting BocomADP (trace_enhance=%s, format=%s)",
        trace_enabled,
        config.logging.enhance.format,
    )
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
