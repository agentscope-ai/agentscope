# -*- coding: utf-8 -*-
"""The main entry point of the human in the loop example."""
import asyncio
import os
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, ToolUseBlock
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
)


def human_permit_function(
    tool_call: ToolUseBlock,
) -> bool:
    """The human permit function that will be called to determine
    1) whether to permit the tool_call to be called, return a bool value
    2) whether to modify the tool_call name and input parameters."""
    arg_name_dict = {
        "execute_python_code": "code",
        "execute_shell_command": "command",
    }
    option = None
    while option not in ["y", "n", "e"]:
        option = (
            input(
                """Enter 'y' for agreement, 'n' for refusal, """
                """'e' to modify execution parameters: """,
            )
            .strip()
            .lower()
        )

    if option == "y":  # execution normally
        return True
    elif option == "n":
        return False
    else:
        # allow the user to modify both the tool and the input parameters
        expected_tool_name = ""
        expected_tool_args = ""
        while expected_tool_name not in [
            "execute_python_code",
            "execute_shell_command",
        ]:
            expected_tool_name = input(
                "Enter the expected tool name registered in the toolkit, "
                "available options: "
                "execute_python_code, execute_shell_command: ",
            ).strip()
        expected_tool_args = input(
            f"Enter {arg_name_dict[expected_tool_name]} "
            f"for {expected_tool_name}: ",
        )  # your code or command

        # modify the tool call block inplace
        tool_call["name"] = expected_tool_name
        tool_call["input"][
            arg_name_dict[expected_tool_name]
        ] = expected_tool_args
        return True


async def main() -> None:
    """The main entry point for the ReAct agent example."""
    toolkit = Toolkit()
    toolkit.register_tool_function(
        execute_shell_command,
        human_permit_func=human_permit_function,
    )
    toolkit.register_tool_function(
        execute_python_code,
        human_permit_func=human_permit_function,
    )

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            enable_thinking=False,
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    msg = Msg(
        "user",
        "What is the version of the agentscope using python?",
        "user",
    )
    await agent(msg)


asyncio.run(main())
