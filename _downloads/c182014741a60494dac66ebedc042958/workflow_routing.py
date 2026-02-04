# -*- coding: utf-8 -*-
"""
.. _routing:

Routing
==========================
There are two ways to implement routing in AgentScope, both simple and easy to implement:

- Routing by structured output
- Routing by tool calls

.. tip:: Considering there is no unified standard/definition for agent routing, we follow the setting in `Building effective agents <https://www.anthropic.com/engineering/building-effective-agents>`_

Routing by Structured Output
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
By this way, we can directly use the structured output of the agent to determine which agent to route the message to.

Initialize a routing agent
"""
import asyncio
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse

router = ReActAgent(
    name="Router",
    sys_prompt="You're a routing agent. Your target is to route the user query to the right follow-up task.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    formatter=DashScopeChatFormatter(),
)


# Use structured output to specify the routing task
class RoutingChoice(BaseModel):
    your_choice: Literal[
        "Content Generation",
        "Programming",
        "Information Retrieval",
        None,
    ] = Field(
        description="Choose the right follow-up task, and choose ``None`` if the task is too simple or no suitable task",
    )
    task_description: str | None = Field(
        description="The task description",
        default=None,
    )


async def example_router_explicit() -> None:
    """Example of explicit routing with structured output."""
    msg_user = Msg(
        "user",
        "Help me to write a poem",
        "user",
    )

    # Route the query
    msg_res = await router(
        msg_user,
        structured_model=RoutingChoice,
    )

    # The structured output is stored in the metadata field
    print("The structured output:")
    print(json.dumps(msg_res.metadata, indent=4, ensure_ascii=False))


asyncio.run(example_router_explicit())

# %%
# Routing by Tool Calls
# ~~~~~~~~~~~~~~~~~~~~~~~~~
# Another way is to wrap the downstream agents into a tool function, so that the routing agent decides which tool to call based on the user query.
#
# We first define several tool functions:
#


async def generate_python(demand: str) -> ToolResponse:
    """Generate Python code based on the demand.

    Args:
        demand (``str``):
            The demand for the Python code.
    """
    # An example demand agent
    python_agent = ReActAgent(
        name="PythonAgent",
        sys_prompt="You're a Python expert, your target is to generate Python code based on the demand.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=Toolkit(),
    )
    msg_res = await python_agent(Msg("user", demand, "user"))

    return ToolResponse(
        content=msg_res.get_content_blocks("text"),
    )


# Fake some other tool functions for demonstration purposes
async def generate_poem(demand: str) -> ToolResponse:
    """Generate a poem based on the demand.

    Args:
        demand (``str``):
            The demand for the poem.
    """
    pass


async def web_search(query: str) -> ToolResponse:
    """Search the web for the query.

    Args:
        query (``str``):
            The query to search.
    """
    pass


# %%
# After that, we define a routing agent and equip it with the above tool functions.
#

toolkit = Toolkit()
toolkit.register_tool_function(generate_python)
toolkit.register_tool_function(generate_poem)
toolkit.register_tool_function(web_search)

# Initialize the routing agent with the toolkit
router_implicit = ReActAgent(
    name="Router",
    sys_prompt="You're a routing agent. Your target is to route the user query to the right follow-up task.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)


async def example_router_implicit() -> None:
    """Example of implicit routing with tool calls."""
    msg_user = Msg(
        "user",
        "Help me to generate a quick sort function in Python",
        "user",
    )

    # Route the query
    await router_implicit(msg_user)


asyncio.run(example_router_implicit())
