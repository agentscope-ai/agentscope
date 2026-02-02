# -*- coding: utf-8 -*-
"""The tool functions used in the planner example."""
import asyncio
import json
import os
from collections import OrderedDict
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
    """
    The result model used for the sub worker to summarize the task result.
    """

    success: bool = Field(
        description="Whether the task was successful or not.",
    )
    message: str = Field(
        description=(
            "The specific task result, should include necessary details, "
            "e.g. the file path if any file is generated, the deviation, "
            "and the error message if any."
        ),
    )


def _convert_to_text_block(msgs: list[Msg]) -> list[TextBlock]:
    # Collect all the content blocks
    blocks: list = []
    # Convert tool_use block into text block for streaming tool response
    for _ in msgs:
        for block in _.get_content_blocks():
            if block["type"] == "text":
                blocks.append(block)

            elif block["type"] == "tool_use":
                blocks.append(
                    TextBlock(
                        type="text",
                        text=f"Calling tool {block['name']} ...",
                    ),
                )

    return blocks


async def create_worker(
    task_description: str,
) -> AsyncGenerator[ToolResponse, None]:
    """Create a sub-worker to finish the given task.

    Args:
        task_description (`str`):
            The description of the task to be done by the sub-worker, should
            contain all the necessary information.

    Returns:
        `AsyncGenerator[ToolResponse, None]`:
            An async generator yielding ToolResponse objects.
    """
    toolkit = Toolkit()

    # Gaode MCP client
    if os.getenv("GAODE_API_KEY"):
        toolkit.create_tool_group(
            group_name="amap_tools",
            description="Map-related tools, including geocoding, routing, and "
            "place search.",
        )
        client = HttpStatelessClient(
            name="amap_mcp",
            transport="streamable_http",
            url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
        )
        await toolkit.register_mcp_client(client, group_name="amap_tools")
    else:
        print(
            "Warning: GAODE_API_KEY not set in environment, skipping Gaode "
            "MCP client registration.",
        )

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
        group_name="browser_tools",
    )

    # GitHub MCP client
    if os.getenv("GITHUB_TOKEN"):
        toolkit.create_tool_group(
            group_name="github_tools",
            description="GitHub related tools, including repository "
            "search and code file retrieval.",
        )
        github_client = HttpStatelessClient(
            name="github",
            transport="streamable_http",
            url="https://api.githubcopilot.com/mcp/",
            headers={"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}"},
        )
        await toolkit.register_mcp_client(
            github_client,
            group_name="github_tools",
        )

    else:
        print(
            "Warning: GITHUB_TOKEN not set in environment, skipping GitHub "
            "MCP client registration.",
        )

    # Basic read/write tools
    toolkit.register_tool_function(write_text_file)
    toolkit.register_tool_function(insert_text_file)
    toolkit.register_tool_function(view_text_file)

    # Create a new sub-agent to finish the given task
    sub_agent = ReActAgent(
        name="Worker",
        sys_prompt=f"""You're an agent named Worker.

## Your Target
Your target is to finish the given task with your tools.

## IMPORTANT
You MUST use the `{ReActAgent.finish_function_name}` to generate the final answer after finishing the task.
""",  # noqa: E501  # pylint: disable=C0301
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        enable_meta_tool=True,
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        max_iters=20,
    )

    # disable the console output of the sub-agent
    sub_agent.set_console_output_enabled(False)

    # Collect the execution process content
    msgs = OrderedDict()

    # Wrap the sub-agent in a coroutine task to obtain the final
    # structured output
    result = []

    async def call_sub_agent() -> None:
        msg_res = await sub_agent(
            Msg(
                "user",
                content=task_description,
                role="user",
            ),
            structured_model=ResultModel,
        )
        result.append(msg_res)

    # Use stream_printing_message to get the streaming response as the
    # sub-agent works
    async for msg, _ in stream_printing_messages(
        agents=[sub_agent],
        coroutine_task=call_sub_agent(),
    ):
        msgs[msg.id] = msg

        # Collect all the content blocks
        yield ToolResponse(
            content=_convert_to_text_block(
                list(msgs.values()),
            ),
            stream=True,
            is_last=False,
        )

        # Expose the interruption signal to the caller
        if msg.metadata and msg.metadata.get("_is_interrupted", False):
            raise asyncio.CancelledError()

    # Obtain the last message from the coroutine task
    if result:
        yield ToolResponse(
            content=[
                *_convert_to_text_block(
                    list(msgs.values()),
                ),
                TextBlock(
                    type="text",
                    text=json.dumps(
                        result[0].metadata,
                        indent=2,
                        ensure_ascii=False,
                    ),
                ),
            ],
            stream=True,
            is_last=True,
        )

    await browser_client.close()
