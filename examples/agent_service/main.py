# -*- coding: utf-8 -*-
"""BocomADP — built on top of AgentScope's ``create_app``.

This is the single place where every concern is wired together:

1. Load config (:mod:`bocomadp.config`).
2. Configure logging once at startup (:func:`configure_logging`).
3. Initialize the framework modules:
   - :class:`ToolRegistry`         — custom tools
   - :class:`MiddlewareRegistry`   — agent middlewares
   - :class:`ProviderManager`       — multi-model routing
   - :class:`HookRegistry`          — 8-phase lifecycle hooks
   - :class:`Runtime`               — 8-phase orchestrator
4. Build the AgentScope app via :func:`create_app` (12 built-in routers).
5. Inject ASGI middlewares via ``extra_middlewares``.
6. Mount custom routers (chat SSE, agent manage, models, health, stats).
7. Register sub-agent templates via ``custom_subagent_templates``.

In addition, enterprise extension cases from ``bankcomm_adp`` are wired in
alongside the core skeleton (kept as runnable examples, not replacing core):
   - ``extra_agent_middlewares``: 审计留痕 + 数据脱敏（DLP）
   - ``extra_agent_tools``:       企业内部工具占位（HR / 文档库 / ITSM）
   - ``platform_health_router``:  平台自有健康检查路由（``/platform/health``）

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
from bocomadp.middleware.error_handler import ErrorHandlingMiddleware
from bocomadp.middleware.request_log import AccessLogMiddleware
from bocomadp.middleware.registry import MiddlewareRegistry
from bocomadp.middleware.agent_middleware import LoggingMiddleware
from bocomadp.providers import ProviderManager
from bocomadp.routers.health import health_router
from bocomadp.routers.stats import stats_router
from bocomadp.routers.chat_sse import chat_sse_router
from bocomadp.routers.agent_manage import (
    agent_manage_router,
    MultiAgentManager,
)
from bocomadp.routers.models import models_router
from bocomadp.runtime import Runtime, HookRegistry
from bocomadp.tools import ToolRegistry

# 企业内部扩展（案例）：管控中间件 + 工具 + 自有路由
# health_router 重命名为 platform_health_router，避免与 bocomadp 的 health_router 同名
from bankcomm_adp.middlewares import build_enterprise_middlewares
from bankcomm_adp.routers import health_router as platform_health_router
from bankcomm_adp.tools import build_enterprise_tools

# ---------------------------------------------------------------------------
# 1. Config + logging
# ---------------------------------------------------------------------------
config = load_config()
configure_logging(config)
logger = logging.getLogger("bocomadp.main")

# ---------------------------------------------------------------------------
# 2. Framework module initialization
# ---------------------------------------------------------------------------
# Tool registry — custom tools injected into every agent
tool_registry = ToolRegistry()
if config.tools.enabled:
    tool_registry.load_builtin_tools()
logger.info("tools loaded: %s", tool_registry.list_tool_names())

# Agent middleware registry — wraps the agent's reply loop
middleware_registry = MiddlewareRegistry()
middleware_registry.register(LoggingMiddleware())

# Provider manager — multi-model routing
provider_manager = ProviderManager()
# Register a placeholder model; replace with real provider setup
# provider_manager.register("openai", OpenAIChatModel(...), model_name="gpt-4o")

# Hook registry — 8-phase lifecycle hooks
hook_registry = HookRegistry()

# Multi-agent manager — agent profile CRUD
multi_agent_manager = MultiAgentManager()

# Runtime — 8-phase orchestrator (wires all registries together)
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
# 3. Default MCP servers attached to every workspace.
# ---------------------------------------------------------------------------
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

if os.getenv("AMAP_API_KEY"):
    default_mcps.append(
        MCPClient(
            name="amap",
            mcp_config=HttpMCPConfig(
                url=f"https://mcp.amap.com/mcp?key={os.environ['AMAP_API_KEY']}",
            ),
            is_stateful=False,
        ),
    )

# AgentScope ``AgentToolFactory`` — returns the same custom tools to
# the built-in ``/chat`` endpoint that ``AgentBuilder`` injects into
# the Runtime layer.  Keeps both agent-creation paths consistent.
# 同时并入 ``bankcomm_adp`` 企业内部工具（HR / 文档库 / ITSM 占位）。
async def _agent_tool_factory(
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
# 4. Storage / message bus / workspace / knowledge base
# ---------------------------------------------------------------------------
storage = RedisStorage(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
)

vector_store = QdrantStore(location=":memory:")

# ---------------------------------------------------------------------------
# 5. Build the app — 12 built-in routers come from create_app automatically.
# ---------------------------------------------------------------------------
trace_enabled = is_trace_correlation_enabled(config)

app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
        default_mcps=default_mcps,
    ),
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN"))],
    custom_subagent_templates=load_subagent_templates(),
    # 企业管控中间件（案例）：审计 + DLP，与 bocomadp 核心中间件并存
    extra_agent_middlewares=build_enterprise_middlewares,
    # 已合并 bocomadp 工具 + bankcomm_adp 企业工具
    extra_agent_tools=_agent_tool_factory,
    title="BocomADP",
    extra_middlewares=[
        # innermost
        Middleware(TraceMiddleware, enabled=trace_enabled),
        Middleware(AccessLogMiddleware, skip_paths=("/healthz", "/readyz")),
        Middleware(ErrorHandlingMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        # outermost
    ],
)

# ---------------------------------------------------------------------------
# 6. Expose framework modules on app.state for routers to access
# ---------------------------------------------------------------------------
app.state.runtime = runtime
app.state.provider_manager = provider_manager
app.state.multi_agent_manager = multi_agent_manager
app.state.tool_registry = tool_registry
app.state.middleware_registry = middleware_registry
app.state.hook_registry = hook_registry

# ---------------------------------------------------------------------------
# 7. Mount custom routers on top of the 12 built-in ones
# ---------------------------------------------------------------------------
app.include_router(health_router)
app.include_router(stats_router)
app.include_router(chat_sse_router)
app.include_router(agent_manage_router)
app.include_router(models_router)
# 企业扩展（案例）平台健康路由：/platform/health（与 /healthz、/readyz 不冲突）
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
