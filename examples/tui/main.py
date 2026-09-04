# -*- coding: utf-8 -*-
"""Run a full-featured AgentScope agent in the Textual terminal UI.

Set ``DASHSCOPE_API_KEY`` and run ``python main.py``. Use Enter to send,
Shift+Enter for a newline, and Ctrl+Q to leave the application.
"""

import argparse
import asyncio
import os

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.tui import launch_tui
from agentscope.workspace import LocalWorkspace


async def main() -> None:
    """Build an Agent with local workspace tools and launch the TUI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument(
        "--workdir",
        default=os.path.join(os.path.dirname(__file__), "workspace"),
    )
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY before running this demo.")

    async with LocalWorkspace(workdir=args.workdir) as workspace:
        agent = Agent(
            name="Friday",
            system_prompt=(
                "You are a helpful assistant named Friday. Use tools when "
                "they help.\n\n" + await workspace.get_instructions()
            ),
            model=DashScopeChatModel(
                credential=DashScopeCredential(api_key=api_key),
                model=args.model,
                stream=True,
            ),
            toolkit=Toolkit(
                tools=await workspace.list_tools(),
                skills_or_loaders=await workspace.list_skills(),
            ),
            offloader=workspace,
        )
        await launch_tui(agent)


if __name__ == "__main__":
    asyncio.run(main())
