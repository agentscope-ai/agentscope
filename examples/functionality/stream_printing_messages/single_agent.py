# -*- coding: utf-8 -*-
"""The example demonstrating how to obtain the messages from the agent in a
streaming way."""
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    view_text_file,
    execute_python_code,
)


async def main() -> None:
    """The main function."""
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        # Change the model and formatter together if you want to try other
        # models
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

    # Prepare a user message
    user_msg = Msg(
        "user",
        "Hi! Who are you?",
        "user",
    )

    # We disable the terminal printing to avoid messy outputs
    agent.set_console_output_enabled(False)

    # obtain the printing messages from the agent in a streaming way
    async for msg, last in stream_printing_messages(
        agents=[agent],
        coroutine_task=agent(user_msg),
    ):
        print(msg, last)


asyncio.run(main())
