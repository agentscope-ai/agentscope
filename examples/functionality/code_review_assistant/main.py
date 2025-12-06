# -*- coding: utf-8 -*-
"""代码审查助手示例 - 展示如何使用 AgentScope 创建能够审查代码的智能助手"""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    view_text_file,
)


async def main() -> None:
    """代码审查助手的主入口点"""
    # 创建工具包
    toolkit = Toolkit()
    toolkit.register_tool_function(view_text_file)
    toolkit.register_tool_function(execute_python_code)

    # 创建代码审查助手智能体
    reviewer = ReActAgent(
        name="CodeReviewer",
        sys_prompt=(
            "You are an expert code reviewer assistant. "
            "You help developers improve their code quality by:\n"
            "- Analyzing code for bugs and potential issues\n"
            "- Suggesting improvements for code readability "
            "and maintainability\n"
            "- Identifying security vulnerabilities\n"
            "- Recommending best practices and design patterns\n"
            "- Providing performance optimization suggestions\n\n"
            "When reviewing code:\n"
            "1. First, read and understand the code structure\n"
            "2. Identify issues in categories: bugs, security, "
            "performance, style\n"
            "3. Provide specific, actionable feedback\n"
            "4. Suggest concrete improvements with examples\n"
            "5. Be constructive and professional in your feedback"
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
    print("代码审查助手已启动！")
    print("你可以尝试以下操作：")
    print("- 审查文件：'请审查 main.py 这个文件'")
    print("- 审查代码片段：直接粘贴代码让助手审查")
    print("- 询问最佳实践：'Python 函数命名的最佳实践是什么？'")
    print("- 输入 'exit' 退出")
    print("=" * 60)
    print()

    # 主对话循环
    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content().strip().lower() == "exit":
            print("\n再见！")
            break
        msg = await reviewer(msg)


if __name__ == "__main__":
    asyncio.run(main())
