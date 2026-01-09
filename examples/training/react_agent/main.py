# -*- coding: utf-8 -*-
"""Example of training a ReAct agent using RL with Trinity-RFT."""
import os
from typing import Dict


from pydantic import BaseModel, Field
from trinity.common.rewards import MathBoxedRewardFn

from agentscope.tune import tune
from agentscope.model import TrinityChatModel
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg


class GSM8KResponseStructure(BaseModel):
    """Response structure for GSM8K tasks."""

    result: str = Field(
        description=(
            "Your solution of the given math problem. "
            "Put your final answer in boxed format, e.g., \\boxed{42}"
        ),
    )


class GSM8KRewardFn(MathBoxedRewardFn):
    """Reward function for GSM8K tasks."""

    def __call__(
        self,
        response: Dict,
        truth: str,
        format_score_coef: float = 0.1,
        **kwargs: Dict,
    ) -> dict[str, float]:
        """Calculate the reward based on the response and truth."""
        # parse GSM8K truth
        if isinstance(truth, str) and "####" in truth:
            truth = truth.split("####")[1].strip()
        else:
            truth = str(truth)
        return super().__call__(
            response=response["result"],
            truth=truth,
            with_think=False,
            format_score_coef=format_score_coef,
            **kwargs,
        )


async def run_react_agent(task: Dict, model: TrinityChatModel) -> float:
    """A simple workflow function using the ReAct agent to solve tasks.

    Args:
        task (Dict): The task to be solved.
        model (TrinityChatModel): The language model to use.

    Returns:
        float: The reward obtained by solving the task.
    """
    sys_prompt = (
        "You are an agent specialized in solving math problems with tools. "
        "Please solve the math problem given to you. You can write and "
        "execute Python code to perform calculation or verify your answer. "
        "You should return your final answer within \\boxed{{}}."
    )

    response_structure = GSM8KResponseStructure
    reward_fn = GSM8KRewardFn()
    agent = ReActAgent(
        name="react_agent",
        sys_prompt=sys_prompt,
        model=model,
        enable_meta_tool=True,
        formatter=OpenAIChatFormatter(),
    )
    response = await agent.reply(
        msg=Msg("user", task["question"], role="user"),
        structured_model=response_structure,
    )
    reward = reward_fn(
        response=response.metadata,
        truth=task["answer"],
    )
    return sum(reward.values())


if __name__ == "__main__":
    config_path = os.path.join(
        os.path.dirname(__file__),
        "config.yaml",
    )
    tune(
        workflow_func=run_react_agent,
        config_path=config_path,
    )
