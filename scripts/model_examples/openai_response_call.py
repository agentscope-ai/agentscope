# -*- coding: utf-8 -*-
"""Examples of OpenAI Responses API model calls.

The Responses API (vs Chat Completions API) provides first-class streaming
events for reasoning/thinking content, making it a natural fit for reasoning
models such as o3 and o4-mini.
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
from agentscope.model import OpenAIResponseModel
from agentscope.credential import OpenAICredential
from agentscope.tool import Toolkit, ToolChoice

# ---------------------------------------------------------------------------
# Example 1: Simple user message (streaming)
# ---------------------------------------------------------------------------


async def example_simple_call() -> None:
    """Call the OpenAI Response model with a simple text message."""
    model = OpenAIResponseModel(
        credential=OpenAICredential(
            api_key=os.environ["OPENAI_API_KEY"],
        ),
        model="o1",
        stream=True,
        context_size=200_000,
        parameters=OpenAIResponseModel.Parameters(reasoning_effort="high"),
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
    """Call the OpenAI Response model with tool calling enabled."""
    toolkit = Toolkit()
    toolkit.register_function(get_weather)
    tools = toolkit.get_function_schemas()

    model = OpenAIResponseModel(
        credential=OpenAICredential(
            api_key=os.environ["OPENAI_API_KEY"],
        ),
        model="o1",
        stream=True,
        context_size=200_000,
        parameters=OpenAIResponseModel.Parameters(reasoning_effort="high"),
    )

    msgs = [
        Msg(
            name="user",
            content="What is the weather in Hangzhou?",
            role="user",
        ),
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
                    # Responses API: use call_id (call_xxx) so that the
                    # function_call_output.call_id matches
                    # function_call.call_id
                    id=tool_call.call_id or tool_call.id,
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
