# -*- coding: utf-8 -*-
"""An example of the goal pipeline."""

import os
import asyncio

from agentscope.state import AgentState
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.pipeline import GoalPipeline
from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.workspace import LocalWorkspace
from agentscope.console import launch_console
from agentscope.tool import Toolkit


async def main() -> None:
    """The main function."""

    # Executor and verifier share the same workspace, so they can read/write
    # files to it.
    async with LocalWorkspace(
        workdir=os.path.join(os.path.dirname(__file__), "workspace"),
    ) as workspace:
        # Create the executor agent
        executor = Agent(
            name="Executor",
            system_prompt="You're a programmer named 'Executor'.",
            model=DashScopeChatModel(
                credential=DashScopeCredential(
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                ),
                model="qwen3.8-max",
            ),
            toolkit=Toolkit(tools=await workspace.list_tools()),
            offloader=workspace,
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.BYPASS,
                ),
            ),
        )

        # Create the verifier agent
        verifier = Agent(
            name="Verifier",
            system_prompt="You're a programmer named 'Verifier'.",
            model=DashScopeChatModel(
                credential=DashScopeCredential(
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                ),
                model="qwen3.8-max",
            ),
            toolkit=Toolkit(tools=await workspace.list_tools()),
            offloader=workspace,
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.BYPASS,
                ),
            ),
        )

        # The goal arrives with the task, not at construction: whatever
        # the pipeline is first asked to do is also what the verifier
        # judges against.
        pipe = GoalPipeline(
            executor=executor,
            verifier=verifier,
        )

        await launch_console(
            agent=pipe,
        )


asyncio.run(main())
