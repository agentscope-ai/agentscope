# -*- coding: utf-8 -*-
"""计算器助手示例 - 展示如何使用 AgentScope 创建能够进行数学计算的智能助手"""
import asyncio
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, execute_python_code


class CalculationResult(BaseModel):
    """计算结果的结构化输出模型"""

    expression: str = Field(description="数学表达式")
    result: float = Field(description="计算结果")
    operation: Literal[
        "addition",
        "subtraction",
        "multiplication",
        "division",
        "power",
        "other",
    ] = Field(
        description="运算类型",
    )


async def main() -> None:
    """计算器助手的主入口点"""
    # 创建工具包
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)

    # 创建计算器助手智能体
    calculator = ReActAgent(
        name="Calculator",
        sys_prompt=(
            "You are a helpful calculator assistant. "
            "You can help users with various mathematical "
            "calculations including:\n"
            "- Basic arithmetic (addition, subtraction, "
            "multiplication, division)\n"
            "- Advanced operations (powers, roots, logarithms)\n"
            "- Mathematical expressions evaluation\n"
            "- Unit conversions\n"
            "Always show your work and explain the calculation steps. "
            "For complex expressions, use Python code to ensure accuracy."
        ),
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            enable_thinking=False,
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    # 创建用户代理
    user = UserAgent("User")

    print("=" * 60)
    print("计算器助手已启动！")
    print("你可以尝试以下操作：")
    print("- 基本运算：'计算 123 + 456'")
    print("- 复杂表达式：'计算 (25 * 4) / 2 + 10'")
    print("- 科学计算：'计算 2 的 10 次方'")
    print("- 数学问题：'如果一个圆的半径是 5，求它的面积'")
    print("- 输入 'exit' 退出")
    print("=" * 60)
    print()

    # 演示：结构化输出
    print("演示：结构化输出计算")
    print("-" * 60)
    demo_query = Msg(
        "user",
        "计算 123 乘以 456 的结果",
        "user",
    )
    result = await calculator(demo_query, structured_model=CalculationResult)
    if result.metadata:
        print("\n结构化计算结果：")
        print(json.dumps(result.metadata, indent=2, ensure_ascii=False))
    print("\n" + "=" * 60 + "\n")

    # 交互式对话循环
    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content().strip().lower() == "exit":
            print("\n再见！")
            break
        msg = await calculator(msg)


if __name__ == "__main__":
    asyncio.run(main())
