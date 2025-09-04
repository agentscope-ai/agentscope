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
from agentscope.mcp import HttpStatelessClient
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


class NumberResult(BaseModel):
    """A simple number result model for structured output."""

    result: int = Field(description="The result of the calculation")


# Initialize MCP clients
add_tool_stateless_client = HttpStatelessClient(
    # The name to identify the MCP
    name="add_tool",
    transport="sse",
    url="http://127.0.0.1:8001/sse_app/sse",
)

multiply_tool_stateless_client = HttpStatelessClient(
    # The name to identify the MCP
    name="multiply_tool",
    transport="streamable_http",
    url="http://127.0.0.1:8001/streamable_http_app/mcp/",
)

# Initialize toolkit
toolkit = Toolkit()


async def register_mcp_tools() -> None:
    """Register all MCP tools from the clients."""
    # Register all tools from the MCP servers
    await toolkit.register_mcp_client(add_tool_stateless_client)
    await toolkit.register_mcp_client(multiply_tool_stateless_client)

    print(
        "Total number of MCP tools registered:",
        len(toolkit.get_json_schemas()),
    )


async def run_calculation_agent() -> None:
    """Run the calculation agent with a math problem."""
    # Initialize the agent
    agent = ReActAgent(
        name="Jarvis",
        sys_prompt="You're a helpful assistant named Jarvis.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        parallel_tool_calls=True,
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
        f"{json.dumps(res.metadata, indent=4)}\n"
        "```",
    )


async def main() -> None:
    """Main function to run the demo."""
    # Register MCP tools
    await register_mcp_tools()

    # Run the calculation agent
    await run_calculation_agent()


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
