# -*- coding: utf-8 -*-
"""The tool functions used in the planner example."""
import json
import os
from typing import AsyncGenerator

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.mcp import HttpStatelessClient, StdIOStatefulClient
from agentscope.message import Msg, TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.tool import (
    ToolResponse,
    Toolkit,
    write_text_file,
    insert_text_file,
    view_text_file,
)


class ResultModel(BaseModel):
    """The result model used for the sub worker to summarize the task result."""

    success: bool = Field(
        description="Whether the task was successful or not.",
    )
    message: str = Field(
        description=(
            "The specific task result, should include necessary details, "
            "e.g. the file path if any file is generated, the deviation, "
            "and the error message if any."
        )
    )


async def create_worker(
    task_description: str,
) -> AsyncGenerator[ToolResponse, None]:
    """Create a sub-worker to finish the given task.

    Args:
        task_description (`str`):
            The description of the task to be done by the sub-worker, should contain all the necessary information.

    Returns:
        `AsyncGenerator[ToolResponse, None]`:
            An async generator yielding ToolResponse objects.
    """
    toolkit = Toolkit()

    # Gaode MCP client
    toolkit.create_tool_group(
        group_name="amap_tools",
        description="Map-related tools, including geocoding, routing, and place search.",
    )
    client = HttpStatelessClient(
        name="amap_mcp",
        transport="streamable_http",
        url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
    )
    await toolkit.register_mcp_client(client, group_name="amap_tools")

    # Browser MCP client
    toolkit.create_tool_group(
        group_name="browser_tools",
        description="Web browsing related tools.",
    )
    browser_client = StdIOStatefulClient(
        name="playwright-mcp",
        command="npx",
        args=["@playwright/mcp@latest"],
    )
    await browser_client.connect()
    await toolkit.register_mcp_client(
        browser_client,
        group_name="browser_tools"
    )

    # GitHub MCP client
    toolkit.create_tool_group(
        group_name="github_tools",
        description="GitHub related tools, including repository search and code file retrieval.",
    )
    github_client = HttpStatelessClient(
        name="github",
        transport="streamable_http",
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}"}
    )
    await toolkit.register_mcp_client(
        github_client,
        group_name="github_tools"
    )

    # Basic read/write tools
    toolkit.register_tool_function(write_text_file)
    toolkit.register_tool_function(insert_text_file)
    toolkit.register_tool_function(view_text_file)

    # Create a new sub-agent to finish the given task
    sub_agent = ReActAgent(
        name="ASAgent",
        sys_prompt="""You're ASAgent, your target is to finish the given task with your tools.""",
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        enable_meta_tool=True,
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    # disable
    # sub_agent.set_console_output_enabled(False)

    # Use stream_printing_message to get the streaming response as the sub-agent works
    async for msg, last in stream_printing_messages(
        agents=[sub_agent],
        coroutine_task=sub_agent(
            Msg(
                "user",
                content=task_description,
                role="user",
            ),
            structured_model=ResultModel,
        )
    ):
        if last:
            yield ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=json.dumps(msg.metadata, indent=2)
                    )
                ],
                stream=True,
                is_last=last,
            )

        else:
            content = []
            # Convert tool_use block into text block for streaming tool response
            for _ in msg.get_content_blocks():
                if _["type"] == "text":
                    content.append(_)
                elif _["type"] == "tool_use":
                    content.append(_)



            yield ToolResponse(
                content=msg.get_content_blocks(),
                stream=True,
                is_last=last,
            )

    await browser_client.close()
