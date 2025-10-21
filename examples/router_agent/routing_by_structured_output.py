# -*- coding: utf-8 -*-
"""Complete router agent with proper feedback for all cases."""
import asyncio
import os
from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

# System prompts
MATH_PROMPT = "You are a math assistant to help solve math problems."

HISTORY_PROMPT = "You are an assistant who is good at history."

ROUTER_PROMPT = """You're a router assistant named {name}.

## YOUR TARGET
Your job is to either route user questions to the appropriate agent or answer them directly.

## DECISION PROCESS
1. Analyze the user's question carefully.
2. Decide whether the question should be handled by a specialized agent or by you directly:
    - If it's a math question, route it to the math agent.
    - If it's a history question, route it to the history agent.
    - For all other questions, answer them directly yourself.

### When you decide to route a question to another agent, you must
    - Not answer the question directly.
    - Provide a brief explanation of why you're routing the question.

## Available Agents
    - Math: Specialized in mathematics, calculations, and numerical problems.
    - History: Specialized in historical events, dates, and historical knowledge.
"""  # noqa


# Pydantic model for structured output
class RouterResponse(BaseModel):
    """Router Response"""

    agent_name: str | None = Field(
        default=None,
        description="The name of the agent to answer the question. Use "
        "'router' when answering directly.",
    )
    routing_reason: str | None = Field(
        default=None,
        description="Explanation of routing decision or the direct answer to "
        "user's question.",
    )


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


async def main() -> None:
    """Main conversation loop."""
    # Initialize agents
    agent_math = create_agent("Math", MATH_PROMPT)
    agent_history = create_agent("History", HISTORY_PROMPT)
    router_agent = create_agent("Router", ROUTER_PROMPT.format(name="Router"))

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
        router_msg = await router_agent(
            user_msg,
            structured_model=RouterResponse,
        )

        # Route based on router's decision
        if router_msg.metadata:
            agent_name = router_msg.metadata.get("agent_name")
            if agent_name == "Math":
                msg = await agent_math(user_msg)
            elif agent_name == "History":
                msg = await agent_history(user_msg)
            else:
                # Router handles the question directly
                msg = router_msg
        else:
            # Fallback to router response
            msg = router_msg


if __name__ == "__main__":
    asyncio.run(main())
