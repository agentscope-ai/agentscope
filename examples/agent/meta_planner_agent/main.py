# -*- coding: utf-8 -*-
"""The planner agent example."""
import asyncio
import os

from tool import create_worker

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.plan import PlanNotebook
from agentscope.tool import Toolkit


async def main() -> None:
    """The main function."""
    # Connect to the studio for better visualization (optional)
    # import agentscope
    # agentscope.init(
    #     project="meta_planner_agent",
    #     studio_url="http://localhost:3000",
    # )

    toolkit = Toolkit()
    toolkit.register_tool_function(create_worker)

    planner = ReActAgent(
        name="Friday",
        # pylint: disable=C0301
        sys_prompt="""You are Friday, a multifunctional agent that can help people solving different complex tasks. You act like a meta planner to solve complicated tasks by decomposing the task and building/orchestrating different worker agents to finish the sub-tasks.

## Core Mission
Your primary purpose is to break down complicated tasks into manageable subtasks (a plan), create worker agents to finish the subtask, and coordinate their execution to achieve the user's goal efficiently.

### Important Constraints
1. DO NOT TRY TO SOLVE THE SUBTASKS DIRECTLY yourself.
2. Always follow the plan sequence.
3. DO NOT finish the plan until all subtasks are finished.
""",  # noqa: E501
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
        plan_notebook=PlanNotebook(),
        toolkit=toolkit,
        max_iters=20,
    )
    user = UserAgent(name="user")

    msg = None
    while True:
        msg = await planner(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break


asyncio.run(main())
