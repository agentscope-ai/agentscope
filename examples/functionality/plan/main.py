# -*- coding: utf-8 -*-
"""The main entry point of the plan example."""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.plan import PlanNotebook
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
    write_text_file,
    insert_text_file,
    view_text_file,
)


async def main() -> None:
    """The main entry point for the plan example."""
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(write_text_file)
    toolkit.register_tool_function(insert_text_file)
    toolkit.register_tool_function(view_text_file)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="""You're a helpful assistant named Friday.

# Target
Your target is to finish the given task with careful planning.

# Note
- You can equip yourself with plan related tools to help you plan and execute the given task.
- For simple tasks you can directly execute them without planning.
- For complex tasks, e.g. programming a website, game, or app, you MUST create a plan first.
- Once a plan is created, try your best to follow the plan and finish the task step by step.
- If the task requires further clarification, ask the user for more information. Otherwise, don't stop until the task is completed.
""",  # noqa
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        plan_notebook=PlanNotebook(),
    )

    user = UserAgent(name="User")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


asyncio.run(main())
