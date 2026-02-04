# -*- coding: utf-8 -*-
"""
Handoffs
========================================

.. figure:: ../../_static/images/handoffs.png
   :width: 80%
   :align: center
   :alt: Orchestrator-Workers Workflow

   *Handoffs example*

It's very simple to implement the Orchestrator-Workers workflow with tool calls in AgentScope.
First, we create a function to allow the orchestrator to create workers dynamically.

"""

import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    ToolResponse,
    Toolkit,
    execute_python_code,
)


# The tool function to create a worker
async def create_worker(
    task_description: str,
) -> ToolResponse:
    """Create a worker to finish the given task. The worker is equipped with python execution tool.

    Args:
        task_description (``str``):
            The description of the task to be finished by the worker.
    """
    # Equip the worker agent with some tools
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)

    # Create a worker agent
    worker = ReActAgent(
        name="Worker",
        sys_prompt="You're a worker agent. Your target is to finish the given task.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )
    # Let the worker finish the task
    res = await worker(Msg("user", task_description, "user"))
    return ToolResponse(
        content=res.get_content_blocks("text"),
    )


async def run_handoffs() -> None:
    """Example of handoffs workflow."""
    # Initialize the orchestrator agent
    toolkit = Toolkit()
    toolkit.register_tool_function(create_worker)

    orchestrator = ReActAgent(
        name="Orchestrator",
        sys_prompt="You're an orchestrator agent. Your target is to finish the given task by decomposing it into smaller tasks and creating workers to finish them.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    # The task description
    task_description = "Execute hello world in Python"

    # Create a worker to finish the task
    await orchestrator(Msg("user", task_description, "user"))


asyncio.run(run_handoffs())
