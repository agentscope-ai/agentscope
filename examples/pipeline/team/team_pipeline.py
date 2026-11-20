# -*- coding: utf-8 -*-
"""Run a planning team with parallel delegation in a team pipeline."""

import asyncio
import os

from agentscope.agent import Agent
from agentscope.console import launch_console
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import TeamMember, TeamPipeline


async def main() -> None:
    """Create the team and start an interactive console."""
    credential = DashScopeCredential(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )
    leader = Agent(
        name="Leader",
        system_prompt=(
            "You coordinate a planning team. For a planning request, "
            "call TeamAssign for Planner and Reviewer in the same response "
            "so they can work concurrently. Ask Planner for an actionable "
            "plan and Reviewer for an independent analysis of constraints, "
            "risks, and recommendations based on the user's requirements. "
            "Wait for both results before giving your final answer. "
            "Members cannot see your conversation, so include all necessary "
            "context in each assignment. Combine their findings into a "
            "practical plan, state any assumptions, and answer in the "
            "user's language."
        ),
        model=DashScopeChatModel(
            credential=credential,
            model="qwen3.8-max",
        ),
    )
    planner = Agent(
        name="Planner",
        system_prompt=(
            "You create actionable plans from the supplied requirements. "
            "Return concrete steps, priorities, and explicit assumptions. "
            "You have no browsing or execution tools; do not claim to have "
            "researched current facts or performed actions."
        ),
        model=DashScopeChatModel(
            credential=credential,
            model="qwen3.8-max",
        ),
    )
    reviewer = Agent(
        name="Reviewer",
        system_prompt=(
            "You independently analyze the supplied requirements for "
            "constraints, risks, and missing information. Return practical "
            "recommendations without waiting for Planner's output. "
            "You have no browsing or execution tools; do not claim to have "
            "verified external facts or performed actions."
        ),
        model=DashScopeChatModel(
            credential=credential,
            model="qwen3.8-max",
        ),
    )
    pipeline = TeamPipeline(
        leader=leader,
        members=[
            TeamMember(
                agent=planner,
                description=(
                    "Proposes an actionable plan based on the user's "
                    "requirements."
                ),
            ),
            TeamMember(
                agent=reviewer,
                description=(
                    "Independently analyzes requirements for constraints, "
                    "risks, and recommendations."
                ),
            ),
        ],
        reset_members=True,
    )
    await launch_console(agent=pipeline)


if __name__ == "__main__":
    asyncio.run(main())
