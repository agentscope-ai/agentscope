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
# Using ReMe Long-Term Memory
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# .. note:: We provide an example of using ReMe long-term memory in the GitHub repository under the ``examples/long_term_memory/reme`` directory.
#
# .. code-block:: python
#     :caption: Example of ReMe long-term memory setup
#
#     from agentscope.memory import ReMePersonalLongTermMemory
#
#     # Create ReMe personal long-term memory instance
#     reme_long_term_memory = ReMePersonalLongTermMemory(
#         agent_name="Friday",
#         user_name="user_123",
#         model=DashScopeChatModel(
#             model_name="qwen3-max",
#             api_key=os.environ.get("DASHSCOPE_API_KEY"),
#             stream=False,
#         ),
#         embedding_model=DashScopeTextEmbedding(
#             model_name="text-embedding-v4",
#             api_key=os.environ.get("DASHSCOPE_API_KEY"),
#             dimensions=1024,
#         ),
#     )
#
#
# The ``ReMePersonalLongTermMemory`` class provides four main methods for long-term memory operations.
# They include ``record_to_memory`` and ``retrieve_from_memory`` for tool calls,
# as well as ``record`` and ``retrieve`` for direct calls.
#
# As an example, we use ``record_to_memory`` to record user preferences.
#
# .. code-block:: python
#     :caption: Example of recording to ReMe long-term memory
#
#     async def test_record_to_memory():
#         """Test record_to_memory tool function interface"""
#         async with reme_long_term_memory:
#             result = await reme_long_term_memory.record_to_memory(
#                 thinking="The user is sharing their travel preferences and habits",
#                 content=[
#                     "I prefer to stay in homestays when traveling to Hangzhou",
#                     "I like to visit the West Lake in the morning",
#                     "I enjoy drinking Longjing tea",
#                 ],
#             )
#             # Extract result text
#             result_text = " ".join(
#                 block.get("text", "")
#                 for block in result.content
#                 if block.get("type") == "text"
#             )
#             print(f"Recording result: {result_text}")
#
#
#
# Then we use ``retrieve_from_memory`` to retrieve related memories.
#
# .. code-block:: python
#     :caption: Example of retrieving from ReMe long-term memory
#
#     async def test_retrieve_from_memory():
#         """Test retrieve_from_memory tool function interface"""
#         async with reme_long_term_memory:
#             # First record some content
#             await reme_long_term_memory.record_to_memory(
#                 thinking="User is sharing travel preferences",
#                 content=[
#                     "I prefer to stay in homestays when traveling to Hangzhou",
#                 ],
#             )
#
#             # Then retrieve
#             result = await reme_long_term_memory.retrieve_from_memory(
#                 keywords=["Hangzhou travel", "tea preference"],
#             )
#             retrieved_text = " ".join(
#                 block.get("text", "")
#                 for block in result.content
#                 if block.get("type") == "text"
#             )
#             print(f"Retrieved memories: {retrieved_text}")
#
#
# Besides the tool function interface, we can also use the ``record`` method to directly record message conversations.
#
# .. code-block:: python
#     :caption: Example of direct recording to ReMe long-term memory
#
#     async def test_record_direct():
#         """Test record direct recording method"""
#         async with reme_long_term_memory:
#             await reme_long_term_memory.record(
#                 msgs=[
#                     Msg(
#                         role="user",
#                         content="I work as a software engineer and prefer remote work",
#                         name="user",
#                     ),
#                     Msg(
#                         role="assistant",
#                         content="Understood! You're a software engineer who values remote work flexibility.",
#                         name="assistant",
#                     ),
#                     Msg(
#                         role="user",
#                         content="I usually start my day at 9 AM with a cup of coffee",
#                         name="user",
#                     ),
#                 ],
#             )
#             print("Successfully recorded conversation messages")
#
#
# Similarly, we use the ``retrieve`` method to retrieve related memories.
#
# .. code-block:: python
#     :caption: Example of direct retrieval from ReMe long-term memory
#
#     async def test_retrieve_direct():
#         """Test retrieve direct retrieval method"""
#         async with reme_long_term_memory:
#             # First record some content
#             await reme_long_term_memory.record(
#                 msgs=[
#                     Msg(
#                         role="user",
#                         content="I work as a software engineer and prefer remote work",
#                         name="user",
#                     ),
#                 ],
#             )
#
#             # Then retrieve
#             memories = await reme_long_term_memory.retrieve(
#                 msg=Msg(
#                     role="user",
#                     content="What do you know about my work preferences?",
#                     name="user",
#                 ),
#             )
#             print(
#                 f"Retrieved memories: {memories if memories else 'No memories found'}",
#             )
#
#
# Integration with ReAct Agent
# ----------------------------------------
# In AgentScope, the ``ReActAgent`` class receives a ``long_term_memory``
# parameter in its constructor, as well as a ``long_term_memory_mode`` parameter.
#
# If ``long_term_memory_mode`` is set to ``agent_control`` or ``both``,
# ``record_to_memory`` and ``retrieve_from_memory`` tool functions will be
# registered, allowing the agent to autonomously manage long-term memory through tool calls.
#
# .. note:: To achieve the best results, the ``"agent_control"`` mode may require
#  additional instructions in the system prompt.
#
# .. code-block:: python
#     :caption: Example of ReAct agent with ReMe long-term memory
#
#     # Create ReAct agent with long-term memory (agent_control mode)
#     async def test_react_agent_with_reme():
#         """Test ReActAgent integration with ReMe personal memory"""
#         async with reme_long_term_memory:
#             agent_with_reme = ReActAgent(
#                 name="Friday",
#                 sys_prompt=(
#                     "You are a helpful assistant named Friday with long-term memory capabilities. "
#                     "\n\n## Memory Management Guidelines:\n"
#                     "1. **Recording Memories**: When users share personal information, preferences, "
#                     "habits, or facts about themselves, ALWAYS record them using `record_to_memory` "
#                     "for future reference.\n"
#                     "\n2. **Retrieving Memories**: BEFORE answering questions about the user's preferences, "
#                     "past information, or personal details, you MUST FIRST call `retrieve_from_memory` "
#                     "to check if you have any relevant stored information. Do NOT rely solely on the "
#                     "current conversation context.\n"
#                     "\n3. **When to Retrieve**: Call `retrieve_from_memory` when:\n"
#                     "   - User asks questions like 'what do I like?', 'what are my preferences?', "
#                     "'what do you know about me?'\n"
#                     "   - User asks about their past behaviors, habits, or preferences\n"
#                     "   - User refers to information they mentioned before\n"
#                     "   - You need context about the user to provide personalized responses\n"
#                     "\nAlways check your memory first before claiming you don't know something about the user."
#                 ),
#                 model=DashScopeChatModel(
#                     model_name="qwen3-max",
#                     api_key=os.environ.get("DASHSCOPE_API_KEY"),
#                     stream=False,
#                 ),
#                 formatter=DashScopeChatFormatter(),
#                 toolkit=Toolkit(),
#                 memory=InMemoryMemory(),
#                 long_term_memory=reme_long_term_memory,
#                 long_term_memory_mode="agent_control",  # Use agent_control mode
#             )
#
#             # User shares preferences
#             msg = Msg(
#                 role="user",
#                 content="When I travel to Hangzhou, I prefer to stay in a homestay",
#                 name="user",
#             )
#             response = await agent_with_reme(msg)
#             print(f"Agent response: {response.get_text_content()}")
#
#             # Clear short-term memory to test long-term memory
#             await agent_with_reme.memory.clear()
#
#             # Query preferences
#             msg2 = Msg(
#                 role="user",
#                 content="what preference do I have?",
#                 name="user",
#             )
#             response2 = await agent_with_reme(msg2)
#             print(f"Agent response: {response2.get_text_content()}")
#
#
# Then we clear the short-term memory and ask the agent about the user's preferences.
#
# .. code-block:: python
#     :caption: Example of retrieving preferences with ReAct agent and ReMe long-term memory
#
#     async def retrieve_reme_preferences():
#         """Retrieve user preferences from long-term memory"""
#         async with reme_long_term_memory:
#             # Create agent (reusing for demonstration completeness)
#             agent_with_reme = ReActAgent(
#                 name="Friday",
#                 sys_prompt="You are an assistant with long-term memory capabilities.",
#                 model=DashScopeChatModel(
#                     api_key=os.environ.get("DASHSCOPE_API_KEY"),
#                     model_name="qwen3-max",
#                     stream=False,
#                 ),
#                 formatter=DashScopeChatFormatter(),
#                 toolkit=Toolkit(),
#                 memory=InMemoryMemory(),
#                 long_term_memory=reme_long_term_memory,
#                 long_term_memory_mode="agent_control",
#             )
#
#             # Clear short-term memory
#             await agent_with_reme.memory.clear()
#             # The agent will remember previous conversations
#             msg2 = Msg("user", "What are my preferences? Answer briefly.", "user")
#             await agent_with_reme(msg2)
#
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
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - Long-term memory implementation based on the mem0 library, supporting vector storage and retrieval.
#     * - ``ReMePersonalLongTermMemory``
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - Personal memory implementation based on the ReMe framework, providing powerful memory management and retrieval capabilities.
#
#
#
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`memory` - Basic memory system
# - :ref:`agent` - ReAct agent
# - :ref:`tool` - Tool system
