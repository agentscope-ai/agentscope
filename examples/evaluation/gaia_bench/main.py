# -*- coding: utf-8 -*-
"""Example of running GAIABench evaluation with AgentScope."""
import argparse
import os
import asyncio
from argparse import ArgumentParser
from typing import Callable

from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.agent import ReActAgent
from agentscope.evaluate import (
    GAIABenchmark,
    Task,
    SolutionOutput,
    FileEvaluatorStorage,
    GeneralEvaluator,
)
from agentscope.tool import Toolkit


def extract_final_answer(final_answer: str) -> str | None:
    """Extract the final answer from the given final answer string."""
    import re

    match = re.search(r"FINAL ANSWER:\s*(.*)", final_answer)
    if match:
        return match.group(1).strip()
    return None


async def react_agent_solution(
    gaia_task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    """Run ReAct agent with the given task in GAIABench.

    Args:
        gaia_task (`Task`):
            Task to run in GAIABench.
        pre_hook (Callable):
            The pre-hook function to save the agent's pre-print messages.
    """
    # Equip tool functions
    toolkit = Toolkit()
    # TODO: Add tools

    task_prompt = """
    You are a general AI assistant. I will ask you a question. Please reason step by step and and finish your answer using the following template:

    FINAL ANSWER: [YOUR FINAL ANSWER]

    Formatting rules for YOUR FINAL ANSWER:
    - If the answer is a number, write it as plain digits without commas, units (such as $ or %), unless otherwise specified.
    - If the answer is a string, do not use articles or abbreviations (e.g., for cities), and write any numbers in plain digits unless instructed otherwise.
    - If the answer is a comma-separated list, apply the above formatting rules to each item depending on whether it is a number or a string.
    """.strip()  # noqa

    # Create a ReAct agent
    agent = ReActAgent(
        name="Friday",
        sys_prompt=task_prompt,
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            stream=False,
            # generate_kwargs={"enable_search": True}
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    agent.register_instance_hook(
        "pre_print",
        "save_logging",
        pre_hook,
    )

    # Execute the agent to solve the task

    question = gaia_task.input
    if gaia_task.metadata["file_path"]:
        question += "\n" + gaia_task.metadata["file_path"]

    msg_input = Msg("user", question, role="user")
    # Print the input by the running agent to call the pre-print hook
    await agent.print(msg_input)
    output_msg = await agent(msg_input)

    # Obtain tool calls sequence
    memory_msgs = await agent.memory.get_memory()
    # Obtain tool_use blocks as trajectory
    traj = []
    for msg in memory_msgs:
        traj.extend(msg.get_content_blocks("tool_use"))

    final_answer = extract_final_answer(output_msg.get_text_content())
    # Wrap into a SolutionOutput
    solution = SolutionOutput(
        success=True,
        output=final_answer,
        trajectory=traj,
    )
    return solution


async def main() -> None:
    """Main function for running GAIABench."""

    # Prepare data and results directories
    def parse_levels(s: str) -> list[int] | str | int:
        """Parse levels"""
        if s == "all":
            return "all"
        try:
            return [int(x) for x in s.split(",")]
        except Exception as e:
            try:
                return int(s)
            except Exception:
                raise argparse.ArgumentTypeError(
                    "level must be int, list of int, or 'all'",
                ) from e

    parser = ArgumentParser()
    parser.add_argument(
        "--data_dir",
        type=str,
        required=True,
        help="Where to save the dataset.",
    )
    parser.add_argument(
        "--levels",
        type=parse_levels,
        default="all",
        help="The level of dataset for evaluation.",
    )
    parser.add_argument(
        "--use_mirror",
        type=bool,
        default=False,
        help="Whether to use mirror to download dataset.",
    )
    parser.add_argument(
        "--result_dir",
        type=str,
        required=True,
        help="Where to save the evaluation results.",
    )
    parser.add_argument(
        "--n_workers",
        type=int,
        default=1,
        help="The number of ray workers to use for evaluation.",
    )
    args = parser.parse_args()

    # Create the evaluator
    #  or GeneralEvaluator, which more suitable for local debug
    evaluator = GeneralEvaluator(
        name="GAIAbench evaluation",
        benchmark=GAIABenchmark(
            data_dir=args.data_dir,
            levels=args.levels,
            use_mirror=args.use_mirror,
        ),
        # Repeat how many times
        n_repeat=1,
        storage=FileEvaluatorStorage(
            save_dir=args.result_dir,
        ),
        # How many workers to use
        n_workers=args.n_workers,
    )

    # Run the evaluation
    await evaluator.run(react_agent_solution)


asyncio.run(main())
