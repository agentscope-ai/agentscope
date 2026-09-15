# -*- coding: utf-8 -*-
"""Examples of Volcengine Ark model calls."""
import asyncio
import json
import os

from pydantic import BaseModel, Field

from _utils import stream_and_collect
from agentscope.credential import VolcengineCredential
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import VolcengineChatModel
from agentscope.tool import FunctionTool, Toolkit, ToolChoice


MODEL_NAME = "doubao-seed-2-1-pro-260628"


async def example_simple_call() -> None:
    """Call a Doubao model with a simple text message."""
    model = VolcengineChatModel(
        credential=VolcengineCredential(
            api_key=os.environ["VOLCENGINE_API_KEY"],
        ),
        model=MODEL_NAME,
        stream=True,
        parameters=VolcengineChatModel.Parameters(thinking_enable=True),
    )
    msgs = [
        Msg(
            name="user",
            content=[TextBlock(text="What is 1 + 1? Answer briefly.")],
            role="user",
        ),
    ]

    print("=== Simple Call ===")
    await stream_and_collect(await model(msgs))


def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: The city name to query the weather for.

    Returns:
        A description of the current weather.
    """
    return f"The weather in {city} is sunny and 25°C."


async def example_tool_call() -> None:
    """Call a Doubao model with tool calling enabled."""
    toolkit = Toolkit(tools=[FunctionTool(get_weather)])
    tools = await toolkit.get_tool_schemas()
    model = VolcengineChatModel(
        credential=VolcengineCredential(
            api_key=os.environ["VOLCENGINE_API_KEY"],
        ),
        model=MODEL_NAME,
        stream=True,
        parameters=VolcengineChatModel.Parameters(thinking_enable=True),
    )
    msgs = [
        Msg(
            name="user",
            content=[TextBlock(text="What is the weather in Beijing?")],
            role="user",
        ),
    ]

    print("=== Tool Call - Round 1 ===")
    response = await stream_and_collect(
        await model(msgs, tools=tools, tool_choice=ToolChoice(mode="auto")),
    )
    print(response)

    tool_calls = [b for b in response.content if isinstance(b, ToolCallBlock)]
    if tool_calls:
        tool_results = []
        for tool_call in tool_calls:
            result = get_weather(**json.loads(tool_call.input))
            tool_results.append(
                ToolResultBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    output=result,
                    state=ToolResultState.SUCCESS,
                ),
            )
        msgs += [
            Msg(name="assistant", content=response.content, role="assistant"),
            Msg(name="tool", content=tool_results, role="assistant"),
        ]

        print("=== Tool Call - Round 2 (Final) ===")
        await stream_and_collect(await model(msgs))


class MathSolution(BaseModel):
    """Structured solution to a math problem."""

    problem: str = Field(description="The original problem statement")
    answer: float = Field(description="The final numeric answer")
    steps: list[str] = Field(
        description="Step-by-step reasoning leading to the answer",
    )


async def example_structured_output() -> None:
    """Call a Doubao model and request structured output."""
    model = VolcengineChatModel(
        credential=VolcengineCredential(
            api_key=os.environ["VOLCENGINE_API_KEY"],
        ),
        model=MODEL_NAME,
        stream=True,
        parameters=VolcengineChatModel.Parameters(thinking_enable=True),
    )
    msgs = [
        Msg(
            name="user",
            content=[
                TextBlock(
                    text=(
                        "Solve this: A train travels at 60 km/h for "
                        "2.5 hours. How far does it travel in km?"
                    ),
                ),
            ],
            role="user",
        ),
    ]

    print("=== Structured Output ===")
    response = await model.generate_structured_output(
        msgs,
        structured_model=MathSolution,
    )
    print(response.content)


if __name__ == "__main__":
    asyncio.run(example_simple_call())
    asyncio.run(example_tool_call())
    asyncio.run(example_structured_output())
