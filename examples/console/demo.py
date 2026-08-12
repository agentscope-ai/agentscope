# -*- coding: utf-8 -*-
"""Try a real agent's ``reply_stream`` in the terminal.

The agent is backed by ``DashScopeChatModel`` and equipped with the
builtin filesystem tools (Bash/Grep/Glob/Read/Write/Edit); the whole
terminal interaction — rendering, tool-call confirmation, Ctrl+C
interruption — is handled by ``launch_console``. Run with::

    export DASHSCOPE_API_KEY=sk-...
    python demo.py [--model qwen-plus] [--verbosity default]
"""
import argparse
import asyncio
import os

from agentscope.agent import Agent
from agentscope.console import launch_console
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Bash,
    Edit,
    Glob,
    Grep,
    Read,
    Toolkit,
    Write,
)


async def main() -> None:
    """The main entry point of the demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument(
        "--verbosity",
        choices=["quiet", "default", "debug"],
        default="default",
    )
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    agent = Agent(
        name="Friday",
        system_prompt=(
            "You are a helpful assistant named Friday. You can operate "
            "on the local filesystem with the Bash/Grep/Glob/Read/Write/"
            "Edit tools. Use the provided tools whenever they help "
            "answering the question."
        ),
        model=DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=args.model,
            stream=True,
        ),
        toolkit=Toolkit(
            tools=[
                Bash(),
                Grep(),
                Glob(),
                Read(),
                Write(),
                Edit(),
            ],
        ),
    )

    await launch_console(agent, verbosity=args.verbosity)


if __name__ == "__main__":
    asyncio.run(main())
