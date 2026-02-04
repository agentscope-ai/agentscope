# -*- coding: utf-8 -*-
"""
.. _state:

State/Session Management
=================================

In AgentScope, the **"state"** refers to the agent status in the running application, including its current system prompt, memory, context, equipped tools, and other information that **change over time**.

To manage the state of an application, AgentScope designs an automatic state registration system and session-level state management, which features:

- Support **automatic state registration** for all variables inherited from ``StateModule``
- Support **manual state registration** with custom serialization/deserialization methods
- Support **session/application-level management**
"""
import asyncio
import json
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.module import StateModule
from agentscope.session import JSONSession
from agentscope.tool import Toolkit

# %%
# State Module
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The ``StateModule`` class is the foundation for state management in AgentScope and provides three basic functions:
#
# .. list-table:: Methods of ``StateModule``
#     :header-rows: 1
#
#     * - Method
#       - Arguments
#       - Description
#     * - ``register_state``
#       - | ``attr_name``,
#         | ``custom_to_json`` (optional),
#         | ``custom_from_json`` (optional)
#       - Register an attribute as its state, with optional serialization/deserialization function.
#     * - ``state_dict``
#       - \-
#       - Get the state dictionary of current object
#     * - ``load_state_dict``
#       - | ``state_dict``,
#         | ``strict`` (optional)
#       - Load the state dictionary to current object
#
# Within an object of ``StateModule``, all the following attributes will be treated as parts of its state:
#
# - the **attributes** that inherit from ``StateModule``
# - the **attributes** registered by the ``register_state`` method
#
# Note the ``StateModule`` supports **NESTED** serialization and deserialization:
#


class ClassA(StateModule):
    def __init__(self) -> None:
        super().__init__()
        self.cnt = 123
        # register cnt attribute as state
        self.register_state("cnt")


class ClassB(StateModule):
    def __init__(self) -> None:
        super().__init__()

        # attribute "a" inherits from StateModule
        self.a = ClassA()

        # register attribute "c" as state manually
        self.c = "Hello, world!"
        self.register_state("c")


obj_b = ClassB()

print("State of obj_b.a:")
print(obj_b.a.state_dict())

print("\nState of obj_b:")
print(json.dumps(obj_b.state_dict(), indent=4))

# %%
# We can observe the state of ``obj_b`` contains the state of its attribute ``a`` automatically.
#
# In AgentScope, the ``AgentBase``, ``MemoryBase``, ``LongTermMemoryBase`` and ``Toolkit`` classes all inherit from ``StateModule``, thus supporting automatic and nested state management.
#

# Creating an agent
agent = ReActAgent(
    name="Friday",
    sys_prompt="You're a assistant named Friday.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),
    memory=InMemoryMemory(),
    toolkit=Toolkit(),
)

initial_state = agent.state_dict()

print("Initial state of the agent:")
print(json.dumps(initial_state, indent=4))

# %%
# Then we change its state by generating a reply message:
#


async def example_agent_state() -> None:
    """Example of agent state management"""
    await agent(Msg("user", "Hello, agent!", "user"))

    print("State of the agent after generating a reply:")
    print(json.dumps(agent.state_dict(), indent=4))


asyncio.run(example_agent_state())

# %%
# Now we recover the state of the agent to its initial state:
#

agent.load_state_dict(initial_state)

print("State after loading the initial state:")
print(json.dumps(agent.state_dict(), indent=4))

# %%
# Session Management
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# In AgentScope, a session refers to a collection of ``StateModule`` in an application, e.g. multiple agents.
#
# AgentScope provides a ``SessionBase`` class with two abstract methods for session
# management: ``save_session_state`` and ``load_session_state``.
# Developers can implement these methods with their own storage solution.
#
# In AgentScope, we provide a JSON based session class ``JSONSession`` that
# stores/loads the session state in/from a JSON file named with the session ID.
#
# Here we show how to use the JSON based session management in AgentScope.
#
# Saving Session State
# -----------------------------------------
#

# change the agent state by generating a reply message
asyncio.run(example_agent_state())

print("\nState of agent:")
print(json.dumps(agent.state_dict(), indent=4))

# %%
# Then we save it to a session file:


session = JSONSession(
    save_dir="./",  # The dir used to save the session files
)


async def example_session() -> None:
    """Example of session management."""
    await session.save_session_state(
        session_id="user_1",  # Use the name as the session id
        agent=agent,
    )

    print("The saved state:")
    with open("./user_1.json", "r", encoding="utf-8") as f:
        print(json.dumps(json.load(f), indent=4))


asyncio.run(example_session())

# %%
# Loading Session State
# -----------------------------------------
# Now we load the session state from the saved file:


async def example_load_session() -> None:
    """Example of loading session state."""

    # we first clear the memory of the agent
    await agent.memory.clear()

    print("Current state of the agent:")
    print(json.dumps(agent.state_dict(), indent=4))

    # then we load the session state
    await session.load_session_state(
        session_id="user_1",
        # The keyword argument must be the same as the one used in `save_session_state`
        agent=agent,
    )
    print("After loading the session state:")
    print(json.dumps(agent.state_dict(), indent=4))


asyncio.run(example_load_session())

# %%
# Now we can see the agent state is restored to the saved state.
#
