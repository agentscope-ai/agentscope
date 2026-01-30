# -*- coding: utf-8 -*-
"""The main entry point of the ReAct agent example."""
import asyncio
import os

import fakeredis

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory, RedisMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
    view_text_file,
)


async def main() -> None:
    """The main entry point for the ReAct agent example."""
    toolkit = Toolkit()

    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    memory = RedisMemory(
        user_id="user_1",
        session_id="session_1",
        host="localhost",
        port=6379,
    )

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            enable_thinking=False,
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=memory,
    )

    user = UserAgent("User")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)

    await memory.close()

    await memory.clear()


asyncio.run(main())
