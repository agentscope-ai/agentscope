# -*- coding: utf-8 -*-
"""The main entry point of the agent skill example."""
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
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

    # To use agent skills, your agent must be equipped with text file viewing
    # tools.
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    # Register the agent skill
    toolkit.register_agent_skill(
        os.path.join(os.path.dirname(__file__), "skill")
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
        memory=InMemoryMemory(),
    )

    # We prepare two questions
    await agent(
        Msg("user", "What skills do you have?", "user")
    )

    # The second question
    await agent(
        Msg(
        "user",
        "How does agentscope handles the tool result?",
        "user",
        )
    )

asyncio.run(main())
