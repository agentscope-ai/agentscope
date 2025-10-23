# -*- coding: utf-8 -*-
""""""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.plan import PlanNotebook
from agentscope.tool import Toolkit
from examples.planner_agent.tool import create_worker


async def main() -> None:
    """The main function."""

    toolkit = Toolkit()
    toolkit.register_tool_function(create_worker)

    planner = ReActAgent(
        name="Friday",
        sys_prompt="""You are Friday, a multifunctional agent that can help people solving different complex tasks. You act like a meta planner to solve complicated tasks by decomposing the task and building/orchestrating different worker agents to finish the sub-tasks.

## Core Mission
Your primary purpose is to break down complicated tasks into manageable subtasks (a plan), create worker agents to finish the subtask, and coordinate their execution to achieve the user's goal efficiently.

### Important Constraints
1. DO NOT TRY TO SOLVE THE SUBTASKS DIRECTLY yourself.
2. ONLY do reasoning and select functions to coordinate.
3. Always follow the roadmap sequence.
4. DO NOT finish until all subtasks are marked with \"Done\" after revising the roadmap.
5. DO NOT read user's provided file directly. Instead, create a worker to do so for you.

## Example Flow
Task: "Create a data visualization from my sales spreadsheet"
1. Clarify specifics (visualization type, data points of interest)
2. Build roadmap (data loading, cleaning, analysis, visualization, export)
3. Create/select appropriate workers for the i-th subtask (e.g., data searcher or processor)
4. Execute worker for the i-th subtask, revising roadmap after the worker finishes
5. Repeat step 3 and 4 until all subtasks are mark as "Done"
5. Generate final response with visualization results
""",
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
        plan_notebook=PlanNotebook(),
        toolkit=toolkit,
    )
    user = UserAgent(name="user")

    msg = None
    while True:
        msg = await planner(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break

asyncio.run(main())
