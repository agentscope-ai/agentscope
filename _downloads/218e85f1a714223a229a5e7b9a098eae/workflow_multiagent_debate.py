# -*- coding: utf-8 -*-
"""
.. _multiagent-debate:

Multi-Agent Debate
========================

Debate workflow simulates a multi-turn discussion between different agents, mostly several solvers and an aggregator.
Typically, the solvers generate and exchange their answers, while the aggregator collects and summarizes the answers.

We implement the examples in `EMNLP 2024`_, where two debater agents will discuss a topic in a fixed order, and express their
arguments based on the previous debate history.
At each round a moderator agent will decide whether the correct answer can be obtained in the current iteration.
"""
import asyncio
import os

from pydantic import Field, BaseModel

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
    DashScopeChatFormatter,
)
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import MsgHub

# Prepare a topic
topic = (
    "The two circles are externally tangent and there is no relative sliding. "
    "The radius of circle A is 1/3 the radius of circle B. Circle A rolls "
    "around circle B one trip back to its starting point. How many times will "
    "circle A revolve in total?"
)


# Create two debater agents, Alice and Bob, who will discuss the topic.
def create_solver_agent(name: str) -> ReActAgent:
    """Get a solver agent."""
    return ReActAgent(
        name=name,
        sys_prompt=f"You're a debater named {name}. Hello and welcome to the "
        "debate competition. It's unnecessary to fully agree with "
        "each other's perspectives, as our objective is to find "
        "the correct answer. The debate topic is stated as "
        f"follows: {topic}.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )


alice, bob = [create_solver_agent(name) for name in ["Alice", "Bob"]]

# Create a moderator agent
moderator = ReActAgent(
    name="Aggregator",
    sys_prompt=f"""You're a moderator. There will be two debaters involved in a debate competition. They will present their answer and discuss their perspectives on the topic:
``````
{topic}
``````
At the end of each round, you will evaluate both sides' answers and decide which one is correct.""",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    # Use multiagent formatter because the moderator will receive messages from more than a user and an assistant
    formatter=DashScopeMultiAgentFormatter(),
)


# A structured output model for the moderator
class JudgeModel(BaseModel):
    """The structured output model for the moderator."""

    finished: bool = Field(
        description="Whether the debate is finished.",
    )
    correct_answer: str | None = Field(
        description="The correct answer to the debate topic, only if the debate is finished. Otherwise, leave it as None.",
        default=None,
    )


async def run_multiagent_debate() -> None:
    """Run the multi-agent debate workflow."""
    while True:
        # The reply messages in MsgHub from the participants will be broadcasted to all participants.
        async with MsgHub(participants=[alice, bob, moderator]):
            await alice(
                Msg(
                    "user",
                    "You are affirmative side, Please express your viewpoints.",
                    "user",
                ),
            )
            await bob(
                Msg(
                    "user",
                    "You are negative side. You disagree with the affirmative side. Provide your reason and answer.",
                    "user",
                ),
            )

        # Alice and Bob doesn't need to know the moderator's message, so moderator is called outside the MsgHub.
        msg_judge = await moderator(
            Msg(
                "user",
                "Now you have heard the answers from the others, have the debate finished, and can you get the correct answer?",
                "user",
            ),
            structured_model=JudgeModel,
        )

        if msg_judge.metadata.get("finished"):
            print(
                "\nThe debate is finished, and the correct answer is: ",
                msg_judge.metadata.get("correct_answer"),
            )
            break


asyncio.run(run_multiagent_debate())


# %%
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`pipeline`
#
# .. _EMNLP 2024:
# Encouraging Divergent Thinking in Large Language Models through Multi-Agent Debate. EMNLP 2024.
#
