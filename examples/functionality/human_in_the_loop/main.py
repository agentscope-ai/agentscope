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
from agentscope.mcp import HttpStatefulClient


def human_permit_function(
    tool_call: ToolUseBlock,
) -> bool:
    """The human permit function that will be called to determine
    1) whether to permit the tool_call to be called, return a bool value
    2) whether to modify the tool_call name and input parameters."""
    arg_name_dict = {
        "execute_python_code": "code",
        "execute_shell_command": "command",
        "add_one": "a",
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
            "add_one",
        ]:
            expected_tool_name = input(
                "Enter the expected tool name registered in the toolkit, "
                "available options: "
                "execute_python_code, execute_shell_command, add_one: ",
            ).strip()
        expected_tool_args = input(
            f"Enter {arg_name_dict[expected_tool_name]} "
            f"for {expected_tool_name}: ",
        )  # your code or command

        # modify the tool call block inplace
        tool_call["name"] = expected_tool_name
        tool_call["input"].clear()
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

    # Create a stateful MCP client to connect to the SSE MCP server
    # note you can also use the stateless client
    add_mcp_client = HttpStatefulClient(
        name="mcp_add_one",
        transport="sse",
        url="http://127.0.0.1:8001/sse",
    )
    # The stateful client must be connected before using
    await add_mcp_client.connect()
    await toolkit.register_mcp_client(
        add_mcp_client,
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

    user_msgs = [
        "What is the version of the agentscope using python?",
        "add 11 and 1 using add_one tool",
    ]

    for user_msg in user_msgs:
        msg = Msg(
            "user",
            user_msg,
            "user",
        )
        print(msg)
        await agent(msg)

    # The stateful MCP client should be disconnected manually to avoid
    # errors during asyncio.run() shutdown.
    await add_mcp_client.close()


asyncio.run(main())
