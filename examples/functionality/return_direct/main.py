# -*- coding: utf-8 -*-
"""Example demonstrating the return_direct feature for tool functions.

The `return_direct` feature allows tool functions to return their output
directly to the user without further LLM processing. This is useful when:
- The tool output is already user-friendly and doesn't need LLM interpretation
- You want to reduce latency by skipping the LLM reasoning step
- The tool performs a final action that should end the conversation loop
"""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock


# Example 1: Tool with return_direct=True at registration time
def get_weather(city: str) -> ToolResponse:
    """Get the current weather for a city.

    This tool returns weather information directly to the user without
    further LLM processing.

    Args:
        city: The name of the city to get weather for.

    Returns:
        ToolResponse with weather information.
    """
    # Simulated weather data
    weather_data = {
        "beijing": "Beijing: Sunny, 25°C, Humidity 40%",
        "shanghai": "Shanghai: Cloudy, 22°C, Humidity 65%",
        "guangzhou": "Guangzhou: Rainy, 28°C, Humidity 85%",
        "shenzhen": "Shenzhen: Partly Cloudy, 27°C, Humidity 70%",
    }

    city_lower = city.lower()
    if city_lower in weather_data:
        result = weather_data[city_lower]
    else:
        result = f"Weather data not available for {city}"

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=result,
            ),
        ],
    )


# Example 2: Tool that dynamically sets return_direct in ToolResponse
def search_database(query: str, return_immediately: bool = False) -> ToolResponse:
    """Search the database for information.

    This tool can optionally return results directly to the user by setting
    return_immediately=True.

    Args:
        query: The search query.
        return_immediately: If True, return results directly without LLM
            processing.

    Returns:
        ToolResponse with search results.
    """
    # Simulated database search
    results = f"Found 3 results for '{query}':\n1. Result A\n2. Result B\n3. Result C"

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=results,
            ),
        ],
        # Dynamically set return_direct based on the parameter
        return_direct=return_immediately,
    )


# Example 3: Regular tool without return_direct (for comparison)
def calculate(expression: str) -> ToolResponse:
    """Calculate a mathematical expression.

    This tool returns the result to the LLM for further processing/explanation.

    Args:
        expression: The mathematical expression to evaluate.

    Returns:
        ToolResponse with calculation result.
    """
    try:
        # Note: In production, use a safer evaluation method
        result = eval(expression)  # noqa: S307
        text = f"Result: {expression} = {result}"
    except Exception as e:
        text = f"Error calculating '{expression}': {e}"

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=text,
            ),
        ],
    )


async def main() -> None:
    """The main entry point demonstrating return_direct feature."""
    toolkit = Toolkit()

    # Register tool with return_direct=True at registration time
    # When this tool is called, its output will be returned directly to user
    toolkit.register_tool_function(
        get_weather,
        return_direct=True,  # <-- Key parameter!
    )

    # Register tool without return_direct at registration,
    # but it can set return_direct dynamically in ToolResponse
    toolkit.register_tool_function(search_database)

    # Register regular tool for comparison
    toolkit.register_tool_function(calculate)

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

    user = UserAgent("User")

    print("=" * 60)
    print("Return Direct Feature Demo")
    print("=" * 60)
    print("\nTry these commands:")
    print("  - 'What's the weather in Beijing?' (return_direct=True)")
    print("  - 'Search for python tutorials' (normal flow)")
    print("  - 'Calculate 2 + 3 * 4' (normal flow with LLM explanation)")
    print("  - Type 'exit' to quit")
    print("=" * 60)

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


if __name__ == "__main__":
    asyncio.run(main())
