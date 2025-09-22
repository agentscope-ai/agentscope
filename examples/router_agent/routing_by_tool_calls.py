# -*- coding: utf-8 -*-
"""Complete router agent with proper feedback for all cases."""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse

# System prompts
MATH_PROMPT = "You are a math assistant to help solve math problems."

HISTORY_PROMPT = "You are an assistant who is good at history."

ROUTER_PROMPT = """You're a router assistant named {name}."""


def create_agent(name: str, sys_prompt: str) -> ReActAgent:
    """Create a ReActAgent with common configuration."""
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=Toolkit(),
        memory=InMemoryMemory(),
    )


async def generate_math_response(question: str) -> ToolResponse:
    """Generate a math response.

    Args:
        question (`str`):
            The input question.
    """
    agent_math = create_agent("Math", MATH_PROMPT)
    msg_res = await agent_math(
        Msg(
            name="User",
            content=question,
            role="user",
        ),
    )
    return ToolResponse(
        content=msg_res.get_content_blocks("text"),
    )


async def generate_history_response(question: str) -> ToolResponse:
    """Generate a history response.

    Args:
        question (`str`):
            The input question.
    """
    agent_history = create_agent("History", HISTORY_PROMPT)
    msg_res = await agent_history(
        Msg(
            name="User",
            content=question,
            role="user",
        ),
    )
    return ToolResponse(
        content=msg_res.get_content_blocks("text"),
    )


async def main() -> None:
    """Main conversation loop."""
    # Initialize agents
    router_agent = create_agent("Router", ROUTER_PROMPT.format(name="Router"))

    router_agent.toolkit.register_tool_function(generate_math_response)
    router_agent.toolkit.register_tool_function(generate_history_response)

    # Initialize user agent
    user = UserAgent(name="user")

    # Start the conversation loop
    msg = None
    while True:
        # Get user input
        user_msg = await user(msg)
        if user_msg.content == "exit":
            break

        # Get router decision with structured output
        msg = await router_agent(user_msg)


if __name__ == "__main__":
    asyncio.run(main())
