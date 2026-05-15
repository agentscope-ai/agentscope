# -*- coding: utf-8 -*-
"""Examples of xAI Grok model calls using the official xai_sdk gRPC client.

Unlike the OpenAI-compatible approach, the xai_sdk provides native access to
xAI-specific features such as server-side agentic tools (web search, X search,
code execution) and extended reasoning control.
"""
import asyncio
import json
import os

from _utils import stream_and_collect
from agentscope.message import (
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import XAIChatModel
from agentscope.credential import XAICredential
from agentscope.tool import Toolkit, ToolChoice

# ---------------------------------------------------------------------------
# Example 1: Simple user message (streaming, with reasoning)
# ---------------------------------------------------------------------------


async def example_simple_call() -> None:
    """Call the xAI reasoning model with a simple text message."""
    model = XAIChatModel(
        credential=XAICredential(
            api_key=os.environ["XAI_API_KEY"],
        ),
        model="grok-3-mini",
        stream=True,
        context_size=131_072,
        parameters=XAIChatModel.Parameters(reasoning_effort="high"),
    )

    msgs = [
        Msg(
            name="user",
            content="What is 1 + 1? Answer briefly.",
            role="user",
        ),
    ]

    print("=== Simple Call ===")
    await stream_and_collect(await model(msgs))


# ---------------------------------------------------------------------------
# Example 2: Tool calling (streaming)
# ---------------------------------------------------------------------------


def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: The city name to query the weather for.

    Returns:
        A description of the current weather.
    """
    return f"The weather in {city} is sunny and 25°C."


async def example_tool_call() -> None:
    """Call the Grok model with tool calling enabled.

    Uses grok-4.3, xAI's flagship mainstream model.
    """
    toolkit = Toolkit()
    toolkit.register_function(get_weather)
    tools = toolkit.get_function_schemas()

    model = XAIChatModel(
        credential=XAICredential(
            api_key=os.environ["XAI_API_KEY"],
        ),
        model="grok-4.3",
        stream=True,
        context_size=1_000_000,
    )

    msgs = [
        Msg(name="user", content="What is the weather in Wuhan?", role="user"),
    ]

    # First call: model decides to call a tool
    print("=== Tool Call - Round 1 ===")
    response = await stream_and_collect(
        await model(msgs, tools=tools, tool_choice=ToolChoice(mode="auto")),
    )
    print(response)

    tool_calls = [b for b in response.content if isinstance(b, ToolCallBlock)]
    if tool_calls:
        tool_result_blocks = []
        for tool_call in tool_calls:
            args = json.loads(tool_call.input)
            result = get_weather(**args)
            tool_result_blocks.append(
                ToolResultBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    output=result,
                    state=ToolResultState.SUCCESS,
                ),
            )

        assistant_msg = Msg(
            name="assistant",
            content=response.content,
            role="assistant",
        )
        tool_result_msg = Msg(
            name="tool",
            content=tool_result_blocks,
            role="assistant",
        )
        msgs = msgs + [assistant_msg, tool_result_msg]

        print("=== Tool Call - Round 2 (Final) ===")
        await stream_and_collect(await model(msgs))


if __name__ == "__main__":
    asyncio.run(example_simple_call())
    asyncio.run(example_tool_call())
