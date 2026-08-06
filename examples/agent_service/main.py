# -*- coding: utf-8 -*-
"""The example script to start the agent service.

本示例在官方入口之上叠加了企业内部扩展（``bankcomm_adp``）：
    - ``extra_agent_middlewares``: 审计留痕 + 数据脱敏（DLP）
    - ``extra_agent_tools``:       企业内部工具占位（HR / 文档库 / ITSM）
    - ``health_router``:           平台自有健康检查路由

认证保持官方默认的 ``X-User-ID`` 头方式（与 ``examples/web_ui`` 前端兼容）。
"""
import os

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app, SubAgentTemplate
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.rag import QdrantStore

# 企业内部扩展：管控中间件 + 工具 + 自有路由 + K8s 沙箱
from bankcomm_adp.config import settings
from bankcomm_adp.middlewares import build_enterprise_middlewares
from bankcomm_adp.routers import health_router
from bankcomm_adp.tools import build_enterprise_tools
from bankcomm_adp.workspace import build_k8s_workspace_manager

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
                url=f"https://mcp.amap.com/mcp?key="
                f"{os.environ['AMAP_API_KEY']}",
            ),
            is_stateful=False,
        ),
    )

storage = RedisStorage(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
)

vector_store = QdrantStore(location=":memory:")

# ── K8s 沙箱 vs 本地工作区 ──
# 生产环境使用 K8s 沙箱（ADP_K8S_ENABLED=true，默认），
# 本地开发可设置 ADP_K8S_ENABLED=false 退回到 LocalWorkspaceManager。
if settings.k8s_enabled:
    # -- K8s 沙箱模式 —— 每个智能体的代码执行在独立的 K8s Pod 中运行。
    # -- 配置通过 ADP_K8S_* 环境变量注入（kubeconfig、镜像、资源等）。
    # -- 推荐配合预构建镜像（跳过 bootstrap），详见 bankcomm_adp/docker/。
    from agentscope.app.message_bus import RedisMessageBus

    workspace_manager = build_k8s_workspace_manager()
    message_bus = RedisMessageBus(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
    )
else:
    # -- 本地模式 —— 工作区直接使用宿主机文件系统（开发/测试用）
    message_bus = InMemoryMessageBus()
    workspace_manager = LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
        default_mcps=default_mcps,
    )

app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,
    # Knowledge base feature — backed by an in-memory Qdrant store. The
    # CollectionPerKbManager allocates one collection per knowledge base,
    # so any embedding dimension is allowed.
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    # Resource hubs the UI browses under /hub. Neither needs credentials
    # of its own — an individual MCP card declares whatever key it wants
    # from the user in its ``inputs_schema``. Passing a ClawHub token
    # only raises the rate limit.
    mcp_hubs=[GitHubMCPHub()],
    skill_hubs=[ClawSkillHub(api_token=os.getenv("CLAWHUB_API_TOKEN"))],
    # 企业管控中间件：审计 + DLP
    extra_agent_middlewares=build_enterprise_middlewares,
    # 企业内部工具：HR / 文档库 / ITSM
    extra_agent_tools=build_enterprise_tools,
    # Customize your own subagent templates
    custom_subagent_templates=[
        SubAgentTemplate(
            type="explorer",
            description=(
                "Read-only agents specialized in exploration tasks. It can "
                "read files but cannot modify, create, or delete them. Use "
                "this agent type when you need to investigate the codebase, "
                "understand its structure, or gather information from files "
                "to support planning—without making any changes."
            ),
            system_prompt_template="""You are {member_name}, an explorer \
agent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

## Responsibilities
- Complete the exploration tasks assigned by the team leader.
- You are read-only: you may inspect files and the codebase, but you must \
never modify, create, or delete anything.

## Reporting
- Always report the task result back to {leader_name} using the TeamSay \
tool, whether the task succeeds or fails.
- Keep your private reasoning private; only share conclusions and findings \
that the leader needs.

Note: `TeamSay` is your ONLY channel to communicate with {leader_name} and \
the other team members. Any other output you produce is invisible to them, \
so anything you want them to see MUST be sent through `TeamSay`.""",
            permission_context=PermissionContext(
                # Read-only
                mode=PermissionMode.EXPLORE,
            ),
        ),
    ],
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)

# 挂载平台自有路由（与 AgentScope 内置路由并列）
app.include_router(health_router)


if __name__ == "__main__":
    # Start the service
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
