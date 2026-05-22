# -*- coding: utf-8 -*-
"""The main entry point of the ReAct agent example."""
import asyncio
import os
import random
import socket

from agentscope.agent import ReActAgent, UserAgent
from agentscope.codeact.code_act_tool_call_server import CodeActToolCallServer
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    ToolResponse,
    execute_shell_command,
    execute_python_code,
    view_text_file,
)


_REMOTE_TOOL_CALL_FUNC_STR = """
async def remote_tool_call(
    tool_name: str,
    tool_args: dict | None = None,
) -> dict:
    \"\"\"Call a CodeActToolCallServer to run the requested tool and return
    the structured output.

    Args:
        tool_name (`str`):
            The name of the tool function to call.
        tool_args (`dict | None`, optional):
            The arguments to pass to the tool function.

    Returns:
        `dict`:
            The output returned by the server, or an empty dict on error.
    \"\"\"
    import httpx

    url = f"http://localhost:PORT_PLACEHOLDER/call_tool"
    payload = {"tool_name": tool_name, "tool_args": tool_args or {}}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            if resp and resp.raise_for_status():
                return resp.json() or {}

            raise ValueError("no response from code act server")
    except Exception as e:
        raise ValueError("cannot get tool result") from e
"""  # noqa: E501

# For demo purpose, this prompt only asks the agent to use CodeAct approach
# in temperature related questions. Idealy the agent should use this approach
# to solve problems whenever feasible.
_CODE_ACT_SYS_PROMPT = f"""
You are a helpful assistant named CodeAct.

You must only write a piece of Python code, run the code, and return the result to user, when user asks about temperature reading.

You must not use any other approach to answer user's temperature questions.

Feel free to handle questions not related to temperature in any appropriate approach.

Here are the instructions on how to write and run the Python code to get the temperature:
Step 1.
Add this `remote_tool_call` function definition to your code first. This function makes a remote tool call, and returns the response of the remote tool.
# function remote_tool_call definition start
{_REMOTE_TOOL_CALL_FUNC_STR}
# function remote_tool_call definition end

Step 2.
Define an async Python function named `solve_problem` in your code. It does not require any input parameter. It returns a str value.

Step 3.
In `solve_problem` function, you must only call `remote_tool_call` function, whenever you need to call a function.

You have 2 functions in your toolkit: get_fahrenheit_temperature and convert_fahrenheit_to_celsius.
You must only use the names and arguments of these 2 functions as the arguments of the remote_tool_call function.

Step 3.
In `solve_problem` function, first use remote_tool_call to call get_fahrenheit_temperature, to get the response. Extract the fahrenheit value from the response.
Then use this fahrenheit value as the tool argument of convert_fahrenheit_to_celsius function, use remote_tool_call to call convert_fahrenheit_to_celsius. Extract the celsius value from the response.

Step 4.
In `solve_problem` write a sentence saying "The current temperature is X C.", replace the 'X' with the celsius value you extracted from Step 3. Return this sentence.

Step 5.
Make sure your code now contains `remote_tool_call` function and `solve_problem` function. Add a `solve_problem` function call in your code as the execution point.

Step 6.
Execute your code by using the `execute_python_code` tool in your toolkit, and collect the returned value. Return this value to user.

When user does not specify the scale of the temperature, or explicitly asks for reading in celsius, return celsius value to the user by following instruction step 1 throught step 6.
When user asks for fahrenheit reading explicitly, just do the remote get_fahrenheit_temperature call, and return the fahrenheit reading to the user directly.
"""  # noqa: E501


def _is_port_in_use(port: int, timeout: int = 1) -> bool:
    """Check if a port is in use (occupied) or not (free)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        in_use = s.connect_ex(("0.0.0.0", port)) == 0
        return in_use


def _find_free_port() -> int:
    for p in range(1024, 49152):
        if not _is_port_in_use(p):
            return p
    raise RuntimeError("No free port found")


def get_fahrenheit_temperature() -> ToolResponse:
    """Return a fahrenheit temperature reading.

    Returns:
        `ToolResponse`:
            The tool response containing the temperature reading.
            The response contains a metadata dict attribute.
            The metadata has a `structured_output` key. Its value is a dict.
            This dict has a key `fahrenheit`, its value is the temperature reading.
    """  # noqa: E501
    value = random.randint(-4, 122)
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="Got the temperature in fahrenheit.",
            ),
        ],
        metadata={
            "success": True,
            "structured_output": {"fahrenheit": value},
        },
    )


def convert_fahrenheit_to_celsius(fahrenheit: int) -> ToolResponse:
    """Convert a fahrenheit reading to celsius reading.

    Args:
        fahrenheit (int):
            The fahrenheit temperature reading.

    Returns:
        `ToolResponse`:
            The tool response containing the celsius temperature reading.
            The response has a metadata dict attribute.
            The metadata has a `structured_output` key, whose value is a dict.
            This dict has a key `celsius`, its value is the celsius temperature reading.
    """  # noqa: E501
    c = int((fahrenheit - 32) * 5 / 9)
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="Convert fahrenheit reading to celsius reading",
            ),
        ],
        metadata={
            "success": True,
            "structured_output": {"celsius": c},
        },
    )


async def main() -> None:
    """The main entry point for the CodeAct agent example."""
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("Please set `DASHSCOPE_API_KEY` environement variable")
        return

    toolkit = Toolkit()

    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)
    toolkit.register_tool_function(get_fahrenheit_temperature)
    toolkit.register_tool_function(convert_fahrenheit_to_celsius)

    port = _find_free_port()
    # Put the server here just for demo purpose.
    # It should be a component inside the Agent.
    code_act_server = CodeActToolCallServer(
        port=port,
        toolkit=toolkit,
    )
    await code_act_server.start()

    # The remote_tool_call function and the solve-problem-by-coding instruction
    # can also be hide inside the agent.
    sys_prompt = _CODE_ACT_SYS_PROMPT.replace("PORT_PLACEHOLDER", str(port))

    agent = ReActAgent(
        name="CodeAct",
        sys_prompt=sys_prompt,
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

    user = UserAgent("User")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


asyncio.run(main())
