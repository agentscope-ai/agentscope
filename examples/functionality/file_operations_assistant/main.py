# -*- coding: utf-8 -*-
"""文件操作助手示例 - 展示如何使用 AgentScope 创建一个能够进行文件操作的智能助手"""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    execute_shell_command,
    view_text_file,
)


async def main() -> None:
    """文件操作助手的主入口点"""
    # 创建工具包并注册文件相关工具
    toolkit = Toolkit()

    # 注册工具函数
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    # 创建文件操作助手智能体
    assistant = ReActAgent(
        name="FileAssistant",
        sys_prompt=(
            "You are a helpful file operations assistant. "
            "You can help users with various file operations including:\n"
            "- Reading and viewing file contents\n"
            "- Searching for files and text within files\n"
            "- Creating, modifying, and deleting files\n"
            "- Organizing files and directories\n"
            "Always provide clear explanations of what you're doing. "
            "Be careful with file operations that might modify or "
            "delete files."
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
    print("文件操作助手已启动！")
    print("你可以尝试以下操作：")
    print("- 查看文件内容：'查看 README.md 的内容'")
    print("- 搜索文件：'在当前目录搜索 Python 文件'")
    print("- 创建文件：'创建一个名为 test.txt 的文件，内容是 Hello World'")
    print("- 运行命令：'列出当前目录的所有文件'")
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
        msg = await assistant(msg)


if __name__ == "__main__":
    asyncio.run(main())
