# -*- coding: utf-8 -*-
"""
AgentScope 2.0 中间件改造示例
==============================

演示如何通过中间件非侵入式地改造 Agent 行为：

1. LoggingMiddleware    - 全链路日志（推理/工具调用/模型调用）
2. SensitiveFilterMiddleware - 输出敏感词过滤
3. DynamicPromptMiddleware   - 动态注入系统提示词（时间/用户信息）
4. TokenCounterMiddleware    - Token 消耗统计

运行：
    python middleware_demo.py
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import AsyncGenerator, Awaitable, Callable

# Windows 终端 GBK 编码兼容处理
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from agentscope.agent import Agent, ReActConfig
from agentscope.credential import DeepSeekCredential
from agentscope.event import EventType
from agentscope.message import UserMsg, TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


# ──────────────────────────────────────────────
# 工具函数（和 custom_tools_agent/main.py 一样）
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
    }
    data = weather_data.get(city)
    if data:
        return json.dumps({"city": city, **data}, ensure_ascii=False)
    return json.dumps({"error": f"暂无 {city} 的天气数据"}, ensure_ascii=False)


def calculate(expression: str) -> str:
    """安全地计算数学表达式。

    Args:
        expression: 数学表达式，如 "2 + 3 * 4"。
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


# ──────────────────────────────────────────────
# 中间件 1: 全链路日志
# ──────────────────────────────────────────────

class LoggingMiddleware(MiddlewareBase):
    """记录 Agent 每一步执行的详细日志。

    拦截点：
    - on_reasoning: 推理阶段（LLM 调用前后）
    - on_acting: 工具执行阶段
    - on_model_call: 原始模型 API 调用
    """

    def __init__(self) -> None:
        self._reasoning_count = 0
        self._acting_count = 0

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """拦截推理阶段，记录 LLM 调用。"""
        self._reasoning_count += 1
        round_num = self._reasoning_count
        print(f"\n{'='*50}")
        print(f"[LOG] 推理轮次 #{round_num} 开始")
        print(f"[LOG] 当前上下文消息数: {len(agent.state.context)}")
        print(f"{'='*50}")

        start = time.time()
        async for event in next_handler():
            yield event
        elapsed = time.time() - start

        print(f"[LOG] 推理轮次 #{round_num} 完成，耗时 {elapsed:.2f}s")

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """拦截工具执行，记录工具名和结果。"""
        tool_call = input_kwargs.get("tool_call")
        tool_name = tool_call.name if tool_call else "unknown"
        tool_input = tool_call.input if tool_call else "{}"

        self._acting_count += 1
        print(f"\n[LOG] 执行工具 #{self._acting_count}: {tool_name}")
        print(f"[LOG] 工具参数: {tool_input}")

        start = time.time()
        async for chunk in next_handler():
            yield chunk
        elapsed = time.time() - start

        print(f"[LOG] 工具 {tool_name} 执行完成，耗时 {elapsed:.2f}s")

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable],
    ):
        """拦截原始模型 API 调用，记录 token 消耗。"""
        messages = input_kwargs.get("messages", [])
        tools = input_kwargs.get("tools", [])

        print(f"\n[LOG] 模型调用 - 消息数: {len(messages)}, 工具数: {len(tools)}")

        result = await next_handler()

        # 如果是流式响应，包装一下以统计 token
        if hasattr(result, "__aiter__"):
            async def wrapped():
                async for chunk in result:
                    if hasattr(chunk, "usage") and chunk.usage:
                        print(f"[LOG] Token 使用: {chunk.usage}")
                    yield chunk
            return wrapped()
        else:
            if hasattr(result, "usage") and result.usage:
                print(f"[LOG] Token 使用: {result.usage}")
            return result


# ──────────────────────────────────────────────
# 中间件 2: 敏感词过滤
# ──────────────────────────────────────────────

class SensitiveFilterMiddleware(MiddlewareBase):
    """过滤 Agent 输出中的敏感词。

    拦截点：
    - on_reasoning: 在文本事件产出后过滤内容
    """

    def __init__(self, sensitive_words: list[str] | None = None) -> None:
        # 示例敏感词列表
        self.sensitive_words = sensitive_words or [
            "密码", "secret", "password", "api_key",
        ]
        self._filtered_count = 0

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """过滤输出中的敏感词。"""
        async for event in next_handler():
            # 如果是文本增量事件，检查并过滤
            if (
                hasattr(event, "type")
                and event.type == EventType.TEXT_BLOCK_DELTA
                and hasattr(event, "delta")
            ):
                original = event.delta
                filtered = original
                for word in self.sensitive_words:
                    if word in filtered:
                        filtered = filtered.replace(word, "***")
                        self._filtered_count += 1
                        print(f"\n[FILTER] 检测到敏感词 '{word}'，已替换")

                if filtered != original:
                    # 创建修改后的事件
                    event = event.model_copy(update={"delta": filtered})

            yield event


# ──────────────────────────────────────────────
# 中间件 3: 动态系统提示词
# ──────────────────────────────────────────────

class DynamicPromptMiddleware(MiddlewareBase):
    """动态注入系统提示词，添加时间、用户信息等上下文。

    拦截点：
    - on_system_prompt: 变换系统提示词
    """

    def __init__(
        self,
        user_name: str = "用户",
        extra_context: dict | None = None,
    ) -> None:
        self.user_name = user_name
        self.extra_context = extra_context or {}

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """在系统提示词末尾注入动态信息。"""
        now = datetime.now()
        time_info = now.strftime("%Y年%m月%d日 %H:%M %A")

        extra_parts = [
            f"\n\n## 当前环境",
            f"- 当前时间: {time_info}",
            f"- 用户名称: {self.user_name}",
        ]

        for key, value in self.extra_context.items():
            extra_parts.append(f"- {key}: {value}")

        return current_prompt + "".join(extra_parts)


# ──────────────────────────────────────────────
# 中间件 4: Token 统计
# ──────────────────────────────────────────────

class TokenCounterMiddleware(MiddlewareBase):
    """统计 Agent 运行过程中的 Token 消耗。

    拦截点：
    - on_model_call: 统计每次模型调用的 token
    - on_reply: 在回复结束时打印汇总
    """

    def __init__(self) -> None:
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable],
    ):
        """统计模型调用的 token。"""
        result = await next_handler()

        # 处理非流式响应
        if hasattr(result, "usage") and result.usage:
            usage = result.usage
            if hasattr(usage, "input_tokens"):
                self.total_input_tokens += usage.input_tokens
            if hasattr(usage, "output_tokens"):
                self.total_output_tokens += usage.output_tokens
            self.call_count += 1

        return result

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """在回复结束时打印 token 统计汇总。"""
        async for event in next_handler():
            yield event

        # 回复结束，打印统计
        print(f"\n{'─'*40}")
        print(f"[STATS] 本次会话 Token 统计:")
        print(f"[STATS]   模型调用次数: {self.call_count}")
        print(f"[STATS]   输入 Token: {self.total_input_tokens}")
        print(f"[STATS]   输出 Token: {self.total_output_tokens}")
        print(f"[STATS]   总计 Token: {self.total_input_tokens + self.total_output_tokens}")
        print(f"{'─'*40}")


# ──────────────────────────────────────────────
# 构建带中间件的 Agent
# ──────────────────────────────────────────────

def create_agent_with_middlewares() -> Agent:
    """创建一个配置了多个中间件的 Agent。"""
    model = DeepSeekChatModel(
        credential=DeepSeekCredential(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        ),
        model="deepseek-v4-flash",
    )

    toolkit = Toolkit(
        tools=[
            FunctionTool(func=get_weather, is_read_only=True),
            FunctionTool(func=calculate, is_read_only=True),
        ],
    )

    # 组合多个中间件（按顺序执行，洋葱模型）
    middlewares = [
        LoggingMiddleware(),           # 全链路日志
        SensitiveFilterMiddleware(),   # 敏感词过滤
        DynamicPromptMiddleware(       # 动态提示词
            user_name="开发者",
            extra_context={"项目": "AgentScope 中间件演示"},
        ),
        TokenCounterMiddleware(),      # Token 统计
    ]

    return Agent(
        name="中间件演示助手",
        system_prompt="你是一个智能助手，可以帮用户查天气、做计算。请用中文回答。",
        model=model,
        toolkit=toolkit,
        middlewares=middlewares,
        state=AgentState(
            permission_context=PermissionContext(mode=PermissionMode.BYPASS),
        ),
        react_config=ReActConfig(max_iters=10),
    )


# ──────────────────────────────────────────────
# 演示
# ──────────────────────────────────────────────

async def demo() -> None:
    """运行中间件演示。"""
    agent = create_agent_with_middlewares()

    questions = [
        "北京天气怎么样？",
        "帮我算 (100 + 200) * 3",
    ]

    for q in questions:
        print(f"\n{'#'*60}")
        print(f"[USER] {q}")
        print(f"{'#'*60}")

        async for evt in agent.reply_stream(UserMsg("user", q)):
            if evt.type == EventType.TEXT_BLOCK_DELTA:
                print(evt.delta, end="", flush=True)
            elif evt.type == EventType.TOOL_CALL_START:
                print(f"\n  [Tool: {evt.tool_call_name}]")

        print("\n")


async def interactive() -> None:
    """交互式聊天。"""
    agent = create_agent_with_middlewares()

    print("=" * 60)
    print("  AgentScope 2.0 中间件改造演示")
    print("  输入 'quit' 退出")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n[You] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        print("[Agent] ", end="", flush=True)

        async for evt in agent.reply_stream(UserMsg("user", user_input)):
            if evt.type == EventType.TEXT_BLOCK_DELTA:
                print(evt.delta, end="", flush=True)
            elif evt.type == EventType.TOOL_CALL_START:
                print(f"\n  [Tool: {evt.tool_call_name}]")

        print()


async def main() -> None:
    if "--demo" in sys.argv:
        await demo()
    else:
        await interactive()


if __name__ == "__main__":
    asyncio.run(main())
