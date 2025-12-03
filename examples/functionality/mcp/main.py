# -*- coding: utf-8 -*-
"""
Demo showcasing ReAct agent with MCP tools using different transports.

This example demonstrates:
- Registering MCP tools with different transports (sse and streamable_http)
- Using a ReAct agent with registered MCP tools
- Getting structured output from the agent

Before running this demo, please execute:
    python mcp_servers.py
"""

import asyncio
import json
import os

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.mcp import HttpStatelessClient, HttpStatefulClient
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


class NumberResult(BaseModel):
    """A simple number result model for structured output."""

    result: int = Field(description="The result of the calculation")


async def main() -> None:
    """The main entry of the MCP example."""

    toolkit = Toolkit()

    # Create a stateful MCP client to connect to the SSE MCP server
    # note you can also use the stateless client
    add_mcp_client = HttpStatefulClient(
        name="add_mcp",
        transport="sse",
        url="http://127.0.0.1:8001/sse",
    )

    # Create a stateless MCP client to connect to the StreamableHTTP MCP server
    # note you can also use the stateful client
    multiply_mcp_client = HttpStatelessClient(
        name="multiply_mcp",
        transport="streamable_http",
        url="http://127.0.0.1:8002/mcp",
    )

    # The stateful client must be connected before using
    await add_mcp_client.connect()

    # Register the MCP clients to the toolkit
    await toolkit.register_mcp_client(add_mcp_client)
    await toolkit.register_mcp_client(multiply_mcp_client)

    # Initialize the agent
    agent = ReActAgent(
        name="Jarvis",
        sys_prompt="You're a helpful assistant named Jarvis.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    # Run the agent with a calculation task
    res = await agent(
        Msg(
            "user",
            "Calculate 2345 multiplied by 3456, then add 4567 to the result,"
            " what is the final outcome?",
            "user",
        ),
        structured_model=NumberResult,
    )

    print(
        "Structured Output:\n"
        "```\n"
        f"{json.dumps(res.metadata, indent=4, ensure_ascii=False)}\n"
        "```",
    )

    # AgentScope also allows developers to obtain the MCP tool as a local
    #  callable object, and use it directly.
    add_tool_function = await add_mcp_client.get_callable_function(
        "add",
        # If wrap the MCP tool result into the ToolResponse object in
        #  AgentScope
        wrap_tool_result=True,
    )

    # Call it manually
    manual_res = await add_tool_function(a=5, b=10)
    print("When manually calling the MCP tool function:")
    print(manual_res)

    # The stateful client should be disconnected manually!
    await add_mcp_client.close()


asyncio.run(main())
