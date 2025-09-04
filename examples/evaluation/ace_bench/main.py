# -*- coding: utf-8 -*-
"""Example of running ACEBench evaluation with AgentScope."""
import os
import asyncio
from argparse import ArgumentParser
from typing import Callable

from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.agent import ReActAgent
from agentscope.evaluate import (
    ACEBenchmark,
    Task,
    SolutionOutput,
    RayEvaluator,
    FileEvaluatorStorage,
    ACEPhone,
)
from agentscope.tool import Toolkit


async def react_agent_solution(
    ace_task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    """Run ReAct agent with the given task in ACEBench.

    Args:
        ace_task (`Task`):
            Task to run in ACEBench.
        pre_hook (Callable):
            The pre-hook function to save the agent's pre-print messages.
    """
    # Equip tool functions
    toolkit = Toolkit()
    for tool, json_schema in ace_task.metadata["tools"]:
        # register the tool function with the given json schema
        toolkit.register_tool_function(tool, json_schema=json_schema)

    # Create a ReAct agent
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday. "
        "Your target is to solve the given task with your tools."
        "Try to solve the task as best as you can.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            stream=False,
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
    msg_input = Msg("user", ace_task.input, role="user")
    # Print the input by the running agent to call the pre-print hook
    await agent.print(msg_input)
    await agent(msg_input)

    # Obtain tool calls sequence
    memory_msgs = await agent.memory.get_memory()
    # Obtain tool_use blocks as trajectory
    traj = []
    for msg in memory_msgs:
        traj.extend(msg.get_content_blocks("tool_use"))

    # Obtain the final state of the phone and travel system
    phone: ACEPhone = ace_task.metadata["phone"]
    final_state = phone.get_current_state()

    # Wrap into a SolutionOutput
    solution = SolutionOutput(
        success=True,
        output=final_state,
        trajectory=traj,
    )
    return solution


async def main() -> None:
    """Main function for running ACEBench."""
    # Prepare data and results directories
    parser = ArgumentParser()
    parser.add_argument(
        "--data_dir",
        type=str,
        required=True,
        help="Where to save the dataset.",
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
    evaluator = RayEvaluator(
        name="ACEbench evaluation",
        benchmark=ACEBenchmark(
            data_dir=args.data_dir,
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
