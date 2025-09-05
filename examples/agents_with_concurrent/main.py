# -*- coding: utf-8 -*-
# pylint: disable=line-too-long
"""Parallel Multi-Perspective Discussion System."""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

# Different perspective system prompts
OPTIMIST_PROMPT = """You are an optimistic thinker who always looks at the bright side of things.
You focus on opportunities, positive outcomes, and potential benefits.
When discussing any topic, you emphasize hope, progress, and positive possibilities.
Keep your responses concise but enthusiastic."""  # noqa

REALIST_PROMPT = """You are a practical realist who focuses on facts, evidence, and balanced perspectives.
You consider both pros and cons, acknowledge limitations, and provide grounded analysis.
You value objective truth and practical considerations over emotions.
Keep your responses factual and balanced."""  # noqa

CRITIC_PROMPT = """You are a critical thinker who questions assumptions and identifies potential problems.
You focus on risks, challenges, and areas that need improvement.
You provide constructive criticism and point out things others might overlook.
Keep your responses thoughtful and questioning."""  # noqa

INNOVATOR_PROMPT = """You are a creative innovator who thinks outside the box and focuses on possibilities.
You love brainstorming solutions, exploring new approaches, and thinking about future potential.
You emphasize creativity, transformation, and breakthrough thinking.
Keep your responses creative and forward-thinking."""  # noqa


def create_agent(name: str, sys_prompt: str) -> ReActAgent:
    """Create a ReActAgent with common configuration."""
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            stream=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=Toolkit(),
        memory=InMemoryMemory(),
    )


async def main() -> None:
    """Main discussion facilitation loop."""
    user = UserAgent(name="user")
    agents = [
        create_agent("Optimist", OPTIMIST_PROMPT),
        create_agent("Realist", REALIST_PROMPT),
        create_agent("Critic", CRITIC_PROMPT),
        create_agent("Innovator", INNOVATOR_PROMPT),
    ]

    print("🎙️  Welcome to the Multi-Perspective Discussion Forum!")
    print(
        "Present any topic and get instant views from 4 different "
        "perspectives:",
    )
    print("😊 Optimist | 🤓 Realist | 🤨 Critic | 💡 Innovator")

    msg = None
    while True:
        msg = await user(msg)

        if msg.content.lower() in ["exit", "quit", "bye"]:
            print("👋 Thanks for the great discussions!")
            break
        # User provided topic
        topic = msg.content

        print(f"🎯 Topic for Discussion: {topic}")
        print(
            f"👥 {len(agents)} perspectives will share their views "
            f"simultaneously...",
        )
        print("=" * 60)

        msg = Msg(
            name="user",
            content=f"Please share your perspective on this topic: "
            f"{topic}",
            role="user",
        )
        # Get all perspectives in parallel
        discussion_tasks = [agent(msg) for agent in agents]
        await asyncio.gather(*discussion_tasks)


if __name__ == "__main__":
    asyncio.run(main())
