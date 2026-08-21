# -*- coding: utf-8 -*-
"""
行业知识问答 Agent 服务
======================

完整的行业知识问答 Agent 示例，包含：
- 7 个自定义工具按 4 组分类（订单组/客户组/发票组/报表组）
- 工具权限按分组分配，不同部门看到不同工具
- 数据权限过滤中间件（管理员）
- 启动时自动为每个用户预创建专用 Agent

启动：
    uv run python examples/industry_agent/main.py

访问：
    - API 文档: http://127.0.0.1:8000/docs
"""
import os
import sys

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.middleware import MiddlewareBase
from agentscope.tool import FunctionTool, ToolBase

# 导入本地模块
import biz_system
from tools import (
    query_order, query_order_statistics, query_customer_orders,
    query_customer,
    query_invoice, query_invoice_by_order,
    query_sales_performance,
)
from middlewares import DataPermissionMiddleware, AuditMiddleware


# ──────────────────────────────────────────────
# 0. 工具分组定义
# ──────────────────────────────────────────────

# 组名 → 函数列表
TOOL_GROUPS: dict[str, list] = {
    "order": [query_order, query_order_statistics, query_customer_orders],
    "customer": [query_customer],
    "invoice": [query_invoice, query_invoice_by_order],
    "report": [query_sales_performance],
}


# ──────────────────────────────────────────────
# 1. 工具工厂函数（分组展开）
# ──────────────────────────────────────────────

async def create_industry_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[ToolBase]:
    """根据用户权限分组返回对应工具。

    Args:
        user_id: 用户ID（从 X-User-ID 请求头获取）
        agent_id: Agent ID
        session_id: Session ID

    Returns:
        该用户可用的工具列表
    """
    print(f"\n[FACTORY] 创建工具: user={user_id}, agent={agent_id}")

    # 从业务系统获取用户的工具分组权限
    groups = biz_system.get_tool_permissions(user_id)
    print(f"[FACTORY] 用户 {user_id} 的工具分组: {groups}")

    # 展开分组 → 工具实例
    tools = []
    for group in groups:
        funcs = TOOL_GROUPS.get(group, [])
        for func in funcs:
            tools.append(FunctionTool(func=func, is_read_only=True))
            print(f"[FACTORY]   [{group}] + {func.__name__}")

    return tools


# ──────────────────────────────────────────────
# 2. 中间件工厂函数
# ──────────────────────────────────────────────

async def create_industry_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """根据用户返回不同的中间件。

    Args:
        user_id: 用户ID
        agent_id: Agent ID
        session_id: Session ID

    Returns:
        该用户的中间件列表
    """
    print(f"[FACTORY] 创建中间件: user={user_id}")

    middlewares = [
        AuditMiddleware(),  # 所有用户都有审计日志
    ]

    # 管理员有数据权限过滤
    if user_id == "user_admin":
        middlewares.append(DataPermissionMiddleware())
        print(f"[FACTORY]   + DataPermissionMiddleware")

    return middlewares


# ──────────────────────────────────────────────
# 3. 创建应用
# ──────────────────────────────────────────────

storage = RedisStorage(
    host="127.0.0.1",
    port=6379,
    protocol=2,
)

app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
    ),
    # 注入自定义工具工厂
    extra_agent_tools=create_industry_tools,
    # 注入自定义中间件工厂
    extra_agent_middlewares=create_industry_middlewares,
    # CORS 配置
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)


# ──────────────────────────────────────────────
# 5. 启动服务
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  行业知识问答 Agent 服务")
    print("=" * 60)
    print()
    print("  API 文档: http://127.0.0.1:8000/docs")
    print()
    print("  使用方式:")
    print("  1. 先运行: uv run python precreate_agents.py")
    print("  2. WebUI 中选对应 Username 即用对应 Agent")
    print("  3. 每个 Agent 的工具集不同（按分组权限）")
    print()
    print("  预创建用户（16个）:")
    print("    销售: user_sales, user_sales_2, user_sales_3")
    print("    财务: user_finance, user_finance_2")
    print("    管理: user_admin")
    print("    HR:   user_hr, user_hr_2")
    print("    技术: user_tech, user_tech_2")
    print("    市场: user_marketing, user_marketing_2")
    print("    客服: user_support, user_support_2")
    print("    物流: user_logistics, user_logistics_2")
    print()
    print("=" * 60)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Windows 上 reload 旧进程残留导致 502
    )
