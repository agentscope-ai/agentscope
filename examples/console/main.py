# -*- coding: utf-8 -*-
"""Try a full-featured agent in the terminal via ``launch_console``.

The agent is backed by ``DashScopeChatModel`` and assembled from a
``LocalWorkspace``: the builtin filesystem tools and the agent skills
both come from the workspace, and the filesystem-backed long-term
memory (``AgenticMemoryMiddleware``) persists durable facts under the
workspace directory across runs. The whole terminal interaction —
rendering, tool-call confirmation, Ctrl+C interruption — is handled
by ``launch_console``. Run with::

    export DASHSCOPE_API_KEY=sk-...
    python main.py [--model qwen3.7-max] [--verbosity default] \
        [--workdir ./workspace]
"""
import argparse
import asyncio
import os

from agentscope.agent import Agent
from agentscope.console import launch_console
from agentscope.credential import DashScopeCredential
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace


async def main() -> None:
    """The main entry point of the demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument(
        "--verbosity",
        choices=["quiet", "default", "debug"],
        default="default",
    )
    parser.add_argument(
        "--workdir",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspace",
        ),
        help="The workspace root directory.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    async with LocalWorkspace(workdir=args.workdir) as workspace:
        agent = Agent(
            name="Friday",
            system_prompt=(
                "You are a helpful assistant named Friday. Use the "
                "provided tools whenever they help answering the "
                "question.\n\n" + await workspace.get_instructions()
            ),
            model=DashScopeChatModel(
                credential=DashScopeCredential(api_key=api_key),
                model=args.model,
                stream=True,
            ),
            toolkit=Toolkit(
                # Filesystem tools and skills both come from the
                # workspace, bound to its backend and skill partition
                tools=await workspace.list_tools(),
                skills_or_loaders=await workspace.list_skills(),
            ),
            middlewares=[
                AgenticMemoryMiddleware(
                    workdir=workspace.workdir,
                    backend=workspace.get_backend(),
                ),
            ],
            # Offload compressed context and oversized tool results
            # into the workspace
            offloader=workspace,
        )
        await launch_console(agent, verbosity=args.verbosity)


if __name__ == "__main__":
    asyncio.run(main())
