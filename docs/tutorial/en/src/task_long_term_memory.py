# -*- coding: utf-8 -*-
"""
.. _long-term-memory:

Long-Term Memory
========================

In AgentScope, we provide a basic class for long-term memory (``LongTermMemoryBase``) and an implementation based on the `mem0 <https://github.com/mem0ai/mem0>`_ library (``Mem0LongTermMemory``).
Together with the design of ``ReActAgent`` class in :ref:`agent` section, we provide two long-term memory modes:

- ``agent_control``: the agent autonomously manages long-term memory by tool calls, and
- ``static_control``: the developer explicitly controls long-term memory operations.

Developers can also use the ``both`` mode, which activates both memory management modes.

.. hint:: These memory modes are suitable for different usage scenarios. Developers can choose the appropriate mode based on their needs.

Using mem0 Long-Term Memory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note:: We provide an example of using mem0 long-term memory in the GitHub repository under the ``examples/long_term_memory/mem0`` directory.

"""

import os
import asyncio

from agentscope.message import Msg
from agentscope.memory import InMemoryMemory
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


# Create mem0 long-term memory instance
from agentscope.memory import Mem0LongTermMemory
from agentscope.embedding import DashScopeTextEmbedding


long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v2",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
    ),
    on_disk=False,
)

# %%
# The ``Mem0LongTermMemory`` class provides two main methods for long-term memory operations:
# ``record`` and ``retrieve``.
# They take a list of messages as input and record/retrieve information from long-term memory.
#
# As an example, we first store a user preference and then retrieve related information from long-term memory.
#


# Basic usage example
async def basic_usage():
    """Basic usage example"""
    # Record memory
    await long_term_memory.record(
        [Msg("user", "I like staying in homestays", "user")],
    )

    # Retrieve memory
    results = await long_term_memory.retrieve(
        [Msg("user", "My accommodation preferences", "user")],
    )
    print(f"Retrieval results: {results}")


asyncio.run(basic_usage())

# %%
# Integration with ReAct Agent
# ----------------------------------------
# In AgentScope, the ``ReActAgent`` class receives a ``long_term_memory``
# parameter in its constructor, as well as a ``long_term_memory_mode`` parameter
# that specifies the long-term memory mode.
#
# If ``long_term_memory_mode`` is set to ``agent_control`` or ``both``, two
# tool functions ``record_to_memory`` and ``retrieve_from_memory`` will be
# registered in the agent's toolkit, allowing the agent to autonomously
# manage long-term memory through tool calls.
#
# .. note:: To achieve the best results, the ``"agent_control"`` mode may require
#  additional instructions in the system prompt.
#

# Create ReAct agent with long-term memory
agent = ReActAgent(
    name="Friday",
    sys_prompt="You are an assistant with long-term memory capabilities.",
    model=DashScopeChatModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model_name="qwen-max-latest",
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="static_control",  # Use static_control mode
)


async def record_preferences():
    """ReAct agent integration example"""
    # Conversation example
    msg = Msg(
        "user",
        "When I travel to Hangzhou, I like staying in homestays",
        "user",
    )
    await agent(msg)


asyncio.run(record_preferences())

# %%
# Then we clear the short-term memory and ask the agent about the user's preferences.
#


async def retrieve_preferences():
    """Retrieve user preferences from long-term memory"""
    # Clear short-term memory
    await agent.memory.clear()
    # The agent will remember previous conversations
    msg2 = Msg("user", "What are my preferences? Answer briefly.", "user")
    await agent(msg2)


asyncio.run(retrieve_preferences())


# %%
# Customizing Long-Term Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope provides the ``LongTermMemoryBase`` base class, which defines the basic
#
# Developers can inherit from ``LongTermMemoryBase`` to implement custom long-term
# memory systems according to their needs：
#
# .. list-table:: Long-term memory classes in AgentScope
#     :header-rows: 1
#
#     * - Class
#       - Abstract Methods
#       - Description
#     * - ``LongTermMemoryBase``
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - - For ``"static_control"`` mode, you must implement the ``record`` and ``retrieve`` methods.
#         - For ``"agent_control"`` mode, the ``record_to_memory`` and ``retrieve_from_memory`` methods must be implemented.
#     * - ``Mem0LongTermMemory``
#       - \-
#       - Long-term memory implementation based on the mem0 library, supporting vector storage and retrieval.
#
#
#
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`memory` - Basic memory system
# - :ref:`agent` - ReAct agent
# - :ref:`tool` - Tool system
