# -*- coding: utf-8 -*-
"""Route a family conversation according to the mother's budget."""
import argparse
import asyncio
import os

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg, UserMsg
from agentscope.model import ChatModelBase, DashScopeChatModel
from agentscope.types import ReplyFinishedReason


BALL_PRICE = 30


async def speak(agent: Agent, incoming: Msg | list[Msg]) -> Msg:
    """Run a turn and pass on the speaker's name and spoken text."""
    reply = await agent.reply(incoming)
    if reply.finished_reason != ReplyFinishedReason.COMPLETED:
        raise RuntimeError(
            f"{agent.name}'s turn ended with {reply.finished_reason}. "
            f"{reply.error or reply.get_text_content()}",
        )
    text = reply.get_text_content()
    print(f"{agent.name}: {text}")
    return UserMsg(name=agent.name, content=text)


async def run_conversation(model: ChatModelBase, mother_budget: int) -> None:
    """Have the parents respond to their son's request for a basketball."""
    son = Agent(
        name="Son",
        system_prompt=(
            "You are Son. Ask for a basketball when prompted. When a parent "
            "answers, acknowledge the plan in one sentence."
        ),
        model=model,
    )
    mother = Agent(
        name="Mother",
        system_prompt=(
            f"You are Mother. You have ${mother_budget} and the ball costs "
            f"${BALL_PRICE}. If you can afford it, offer to buy it with Son. "
            "Otherwise, ask Father to take Son to the store."
        ),
        model=model,
    )
    father = Agent(
        name="Father",
        system_prompt="You are Father. Tell Son when to meet you at the shop.",
        model=model,
    )
    daughter = Agent(
        name="Daughter",
        system_prompt="You are Daughter. Listen for the family's final plan.",
        model=model,
    )

    request = await speak(
        son,
        UserMsg(
            name="Narrator",
            content=f"Ask Mother for a ${BALL_PRICE} basketball to practice.",
        ),
    )
    answer = await speak(mother, request)
    replies = [answer]
    if mother_budget < BALL_PRICE:
        replies.append(await speak(father, [request, answer]))
    plan = await speak(son, replies)
    await daughter.observe(plan)


def main() -> None:
    """Read the budget and run the conversation with DashScope."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mother-budget",
        type=int,
        default=10,
        help=f"Budget for a ${BALL_PRICE} ball (default %(default)s dollars).",
    )
    args = parser.parse_args()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        parser.error("Set DASHSCOPE_API_KEY to run the example.")

    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen-plus",
        formatter=DashScopeMultiAgentFormatter(),
        stream=False,
    )
    asyncio.run(run_conversation(model, args.mother_budget))


if __name__ == "__main__":
    main()
