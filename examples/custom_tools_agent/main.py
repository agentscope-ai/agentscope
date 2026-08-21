# -*- coding: utf-8 -*-
"""
AgentScope 2.0 自定义工具 Agent 示例
====================================

演示如何：
1. 用 FunctionTool 将普通 Python 函数注册为 Agent 工具
2. 组合内置工具 + 自定义工具
3. 通过事件流式输出 Agent 的执行过程
4. 使用 DeepSeek V4 Flash 模型（可替换为任意提供商）

运行前设置环境变量：
    export DEEPSEEK_API_KEY="sk-xxx"

运行：
    python main.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime

# Windows 终端 GBK 编码兼容处理
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from agentscope.agent import Agent, ReActConfig
from agentscope.credential import DeepSeekCredential
from agentscope.event import EventType
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


# ──────────────────────────────────────────────
# 1. 定义自定义工具函数
# ──────────────────────────────────────────────

def get_weather(city: str) -> str:
    """查询指定城市的天气信息。

    Args:
        city: 城市名称，如 "北京"、"上海"。

    Returns:
        天气信息的 JSON 字符串。
    """
    # 模拟天气数据，实际项目中可调用真实 API
    weather_data = {
        "北京": {"temp": 28, "condition": "晴", "humidity": 45},
        "上海": {"temp": 31, "condition": "多云", "humidity": 72},
        "广州": {"temp": 33, "condition": "雷阵雨", "humidity": 85},
        "深圳": {"temp": 32, "condition": "阴", "humidity": 78},
    }
    data = weather_data.get(city)
    if data:
        return json.dumps(
            {"city": city, **data}, ensure_ascii=False,
        )
    return json.dumps(
        {"error": f"暂无 {city} 的天气数据"}, ensure_ascii=False,
    )


def calculate(expression: str) -> str:
    """安全地计算数学表达式。

    Args:
        expression: 数学表达式，如 "2 + 3 * 4"、"100 / 7"。

    Returns:
        计算结果的 JSON 字符串。
    """
    # 仅允许安全的数学运算
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
        return json.dumps(
            {"error": f"计算失败: {e}"}, ensure_ascii=False,
        )


def get_current_time() -> str:
    """获取当前日期和时间。

    Returns:
        当前时间的 JSON 字符串。
    """
    now = datetime.now()
    return json.dumps(
        {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        },
        ensure_ascii=False,
    )


# ──────────────────────────────────────────────
# 2. 构建 Agent
# ──────────────────────────────────────────────

def create_agent() -> Agent:
    """创建并返回一个配置好的 Agent 实例。"""
    model = DeepSeekChatModel(
        credential=DeepSeekCredential(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        ),
        model="deepseek-v4-flash",
    )

    toolkit = Toolkit(
        tools=[
            # 自定义工具
            FunctionTool(func=get_weather, is_read_only=True),
            FunctionTool(func=calculate, is_read_only=True),
            FunctionTool(func=get_current_time, is_read_only=True),
        ],
    )

    return Agent(
        name="小助手",
        system_prompt=(
            "你是一个智能助手，可以帮用户查天气、做计算、查时间。\n"
            "请用中文回答，语气友好简洁。\n"
            "如果用户的问题不需要工具，直接回答即可。"
        ),
        model=model,
        toolkit=toolkit,
        state=AgentState(
            permission_context=PermissionContext(mode=PermissionMode.BYPASS),
        ),
        react_config=ReActConfig(max_iters=10),
    )


# ──────────────────────────────────────────────
# 3. 流式交互 & 事件处理
# ──────────────────────────────────────────────

async def chat_loop(agent: Agent) -> None:
    """交互式聊天循环，流式展示 Agent 的执行过程。"""
    print("=" * 50)
    print("  AgentScope 2.0 自定义工具 Agent 示例")
    print("  输入 'quit' 或 'exit' 退出")
    print("=" * 50)
    print()

    while True:
        try:
            user_input = input("[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("再见！")
            break

        print("[Agent] ", end="", flush=True)

        async for evt in agent.reply_stream(UserMsg("user", user_input)):
            match evt.type:
                case EventType.TEXT_BLOCK_DELTA:
                    # 文本增量输出
                    print(evt.delta, end="", flush=True)

                case EventType.TOOL_CALL_START:
                    print(f"\n  [Tool] {evt.tool_call_name}")

                case EventType.TOOL_RESULT_TEXT_DELTA:
                    print(f"  [Result] {evt.delta}")

                case EventType.THINKING_BLOCK_DELTA:
                    # 思考过程（部分模型支持）
                    pass

                case EventType.EXCEED_MAX_ITERS:
                    print("\n  [WARN] Max iterations reached.")

        print("\n")


# ──────────────────────────────────────────────
# 4. 非交互式单次调用示例
# ──────────────────────────────────────────────

async def single_call_demo() -> None:
    """演示非交互式单次调用。"""
    agent = create_agent()

    questions = [
        "现在几点了？",
        "北京今天天气怎么样？",
        "帮我算一下 (123 + 456) * 7 等于多少",
        "上海和广州哪个更热？差几度？",
    ]

    for q in questions:
        print(f"\n[Q] {q}")
        print("[A] ", end="", flush=True)

        async for evt in agent.reply_stream(UserMsg("user", q)):
            if evt.type == EventType.TEXT_BLOCK_DELTA:
                print(evt.delta, end="", flush=True)
            elif evt.type == EventType.TOOL_CALL_START:
                print(f"\n  [Tool: {evt.tool_call_name}] ", end="")

        print()


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

async def main() -> None:
    if "--demo" in sys.argv:
        await single_call_demo()
    else:
        agent = create_agent()
        await chat_loop(agent)


if __name__ == "__main__":
    asyncio.run(main())
