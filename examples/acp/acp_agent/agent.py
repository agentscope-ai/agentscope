# -*- coding: utf-8 -*-
"""Expose a coding assistant to QwenPaw or another ACP client."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agentscope.acp import ACPSessionContext, serve_acp
from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.tool import Bash, Edit, Glob, Grep, PowerShell, Read, Toolkit
from agentscope.tool import Write


@asynccontextmanager
async def create_agent(context: ACPSessionContext) -> AsyncIterator[Agent]:
    """Build an independent agent without creating project metadata files."""
    cwd = str(context.cwd)
    shell = PowerShell(cwd=cwd) if os.name == "nt" else Bash(cwd=cwd)
    yield Agent(
        name="AgentScope",
        system_prompt=(
            f"You are a coding assistant. The project directory is {cwd}. "
            f"Use absolute paths for file tools. Ask before making changes."
        ),
        model=DashScopeChatModel(
            credential=DashScopeCredential(
                api_key=os.environ["DASHSCOPE_API_KEY"],
            ),
            model=os.environ.get("AGENTSCOPE_MODEL", "qwen3.7-max"),
            stream=True,
        ),
        toolkit=Toolkit(
            tools=[shell, Read(), Write(), Edit(), Glob(), Grep()],
        ),
    )


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise SystemExit("Set DASHSCOPE_API_KEY before starting the agent")
    asyncio.run(serve_acp(create_agent))
