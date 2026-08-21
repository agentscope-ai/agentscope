# -*- coding: utf-8 -*-
"""
AgentScope 2.0 自定义 Agent 部署服务
=====================================

将自定义工具和中间件集成到 Agent Service，通过 Web UI 或 API 对外提供服务。

启动前准备：
1. 启动 Redis
   Start-Process -FilePath "C:\\googoe\\Redis-x64-5.0.14.1\\redis-server.exe" -WindowStyle Hidden

2. 设置环境变量（可选，用于默认凭证）
   $env:DEEPSEEK_API_KEY="sk-xxx"

启动：
   uv run python examples/my_agent_service/main.py

访问：
   - Web UI: http://localhost:5173 (需单独启动前端)
   - API 文档: http://localhost:8000/docs
"""
import json
import os
import sys
from datetime import datetime
from typing import AsyncGenerator

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.tool import FunctionTool, ToolBase


# ──────────────────────────────────────────────
# 1. 自定义工具函数
# ──────────────────────────────────────────────

def get_weather(city: str) -> str:
    """查询指定城市的天气信息。

    Args:
        city: 城市名称，如 "北京"、"上海"。
    """
    weather_data = {
        "北京": {"temp": 28, "condition": "晴", "humidity": 45},
        "上海": {"temp": 31, "condition": "多云", "humidity": 72},
        "广州": {"temp": 33, "condition": "雷阵雨", "humidity": 85},
        "深圳": {"temp": 32, "condition": "阴", "humidity": 78},
        "杭州": {"temp": 29, "condition": "小雨", "humidity": 68},
    }
    data = weather_data.get(city)
    if data:
        return json.dumps({"city": city, **data}, ensure_ascii=False)
    return json.dumps({"error": f"暂无 {city} 的天气数据"}, ensure_ascii=False)


def calculate(expression: str) -> str:
    """安全地计算数学表达式。

    Args:
        expression: 数学表达式，如 "2 + 3 * 4"、"100 / 7"。
    """
    allowed = set("0123456789+-*/.() %")
    if not all(c in allowed for c in expression.replace(" ", "")):
        return json.dumps({"error": "包含不允许的字符"}, ensure_ascii=False)
    try:
        result = eval(expression, {"__builtins__": {}})  # noqa: S307
        return json.dumps(
            {"expression": expression, "result": result},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"计算失败: {e}"}, ensure_ascii=False)


def get_current_time() -> str:
    """获取当前日期和时间。"""
    now = datetime.now()
    return json.dumps(
        {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        },
        ensure_ascii=False,
    )


# ──────────────────────────────────────────────
# 2. 自定义中间件
# ──────────────────────────────────────────────

class LoggingMiddleware(MiddlewareBase):
    """简单的日志中间件，记录工具调用。"""

    async def on_acting(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ) -> AsyncGenerator:
        tool_call = input_kwargs.get("tool_call")
        tool_name = tool_call.name if tool_call else "unknown"
        print(f"\n[MY_AGENT] 执行工具: {tool_name}")

        async for chunk in next_handler():
            yield chunk

        print(f"[MY_AGENT] 工具 {tool_name} 执行完成")


class DynamicPromptMiddleware(MiddlewareBase):
    """动态注入系统提示词。"""

    async def on_system_prompt(self, agent, current_prompt: str) -> str:
        now = datetime.now()
        time_info = now.strftime("%Y年%m月%d日 %H:%M")
        return current_prompt + f"\n\n当前时间: {time_info}"


# ──────────────────────────────────────────────
# 3. 工厂函数（Agent Service 调用）
# ──────────────────────────────────────────────

async def create_my_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[ToolBase]:
    """为每个会话创建自定义工具。

    可以根据 user_id 返回不同的工具（如权限控制）。
    """
    print(f"[MY_AGENT] 创建工具: user={user_id}, agent={agent_id}")
    return [
        FunctionTool(func=get_weather, is_read_only=True),
        FunctionTool(func=calculate, is_read_only=True),
        FunctionTool(func=get_current_time, is_read_only=True),
    ]


async def create_my_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """为每个会话创建自定义中间件。"""
    print(f"[MY_AGENT] 创建中间件: user={user_id}, agent={agent_id}")
    return [
        LoggingMiddleware(),
        DynamicPromptMiddleware(),
    ]


# ──────────────────────────────────────────────
# 4. 创建应用
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
    # 注入自定义工具
    extra_agent_tools=create_my_tools,
    # 注入自定义中间件
    extra_agent_middlewares=create_my_middlewares,
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
    print("  我的 Agent 服务")
    print("=" * 60)
    print()
    print("  API 文档: http://localhost:8000/docs")
    print("  Web UI:   http://localhost:5173 (需启动前端)")
    print()
    print("  使用步骤:")
    print("  1. 在 Web UI 添加凭证 (API Key)")
    print("  2. 创建 Agent")
    print("  3. 开始对话（自动使用自定义工具和中间件）")
    print()
    print("=" * 60)

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
