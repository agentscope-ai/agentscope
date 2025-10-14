# -*- coding: utf-8 -*-
"""Example of running GAIABench evaluation with AgentScope."""
import argparse
import os
import asyncio
from argparse import ArgumentParser
from typing import Callable

from agentscope.mcp import StdIOStatefulClient
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.agent import ReActAgent
from agentscope.evaluate import (
    GAIABenchmark,
    Task,
    SolutionOutput,
    FileEvaluatorStorage,
    RayEvaluator,
)
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    view_text_file,
    execute_shell_command,
    read_file_with_pandas,
    view_docx_file,
)


def extract_final_answer(final_answer: str) -> str | None:
    """Extract the final answer from the given final answer string."""
    import re

    match = re.search(r"FINAL ANSWER:\s*(.*)", final_answer)
    if match:
        return match.group(1).strip()
    return None


# pylint: disable=too-many-statements
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

    try:
        # Equip tool functions
        toolkit = Toolkit()

        toolkit.register_tool_function(execute_python_code)
        toolkit.register_tool_function(execute_shell_command)
        toolkit.register_tool_function(view_text_file)
        toolkit.register_tool_function(read_file_with_pandas)
        toolkit.register_tool_function(view_docx_file)

        # Create MCP clients for pptx, see
        # https://github.com/GongRzhe/Office-PowerPoint-MCP-Server
        pptx_client = StdIOStatefulClient(
            name="ppt",
            command="uvx",
            args=["--from", "office-powerpoint-mcp-server", "ppt_mcp_server"],
            env={},
        )
        await pptx_client.connect()
        await toolkit.register_mcp_client(pptx_client)
        print("✅ PPT client connected and registered.")

        # Create MCP clients for browser use, see
        # https://github.com/microsoft/playwright-mcp
        browser_client = StdIOStatefulClient(
            name="playwright-mcp",
            command="npx",
            args=["@playwright/mcp@latest"],
        )

        await browser_client.connect()
        await toolkit.register_mcp_client(browser_client)
        print("✅ Browser client connected and registered.")

        # Create MCP clients for tavily search, see
        # https://docs.tavily.com/documentation/mcp
        tavily_search_client = StdIOStatefulClient(
            name="tavily_mcp",
            command="npx",
            args=["-y", "tavily-mcp@latest"],
            env={"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", "")},
        )

        await tavily_search_client.connect()
        await toolkit.register_mcp_client(tavily_search_client)
        print("✅ Tavily client connected and registered.")

        # GAIA evaluation system prompt, see
        # https://huggingface.co/spaces/gaia-benchmark/leaderboard
        task_prompt = """
You are a general AI assistant. I will ask you a question. Report your thoughts, and finish your answer with the following template: FINAL ANSWER: [YOUR FINAL ANSWER]. YOUR FINAL ANSWER should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. If you are asked for a number, don't use comma to write your number neither use units such as $ or percent sign unless specified otherwise. If you are asked for a string, don't use articles, neither abbreviations (e.g. for cities), and write the digits in plain text unless specified otherwise. If you are asked for a comma separated list, apply the above rules depending of whether the element to be put in the list is a number or a string.
""".strip()  # noqa

        # Create a ReAct agent
        agent = ReActAgent(
            name="Friday",
            sys_prompt=task_prompt,
            model=DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model_name="qwen-max",
                stream=True,
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
            question += "\n" + os.path.abspath(gaia_task.metadata["file_path"])

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

    except Exception as e:
        print(f"An error occurred: {e}")
        print("Cleaning up clients...")
        return SolutionOutput(
            success=False,
            output=None,
            trajectory=[],
            meta={"error": str(e)},
        )
    finally:
        # Ensure both clients are always closed,
        # regardless of success or failure
        try:
            await tavily_search_client.close()
            print("Tavily search client closed successfully.")
        except Exception as e:
            print(f"An error occurred during tavily cleanup: {e}")
        except BaseException as cleanup_error:
            print(f"Error while closing tavily search client: {cleanup_error}")

        try:
            await browser_client.close()
            print("Browser client closed successfully.")
        except Exception as cleanup_error:
            print(f"Error while closing browser client: {cleanup_error}")

        try:
            await pptx_client.close()
            print("PPT client closed successfully.")
        except Exception as e:
            print(f"An error occurred during ppt cleanup: {e}")


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
    evaluator = RayEvaluator(
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
