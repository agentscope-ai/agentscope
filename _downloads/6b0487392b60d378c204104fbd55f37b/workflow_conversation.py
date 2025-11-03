# -*- coding: utf-8 -*-
"""
.. _conversation:

Conversation
======================

Conversation is a design pattern that agents exchange and share information
between each other, most commonly in game playing, chatbot, and multi-agent
discussion scenarios.

In AgentScope, the conversation is built upon the **explicit message
exchange**. In this tutorial, we will demonstrate how to build a conversation

- between a user and an agent (chatbot)
- between multiple agents (game playing, discussion, etc.)

Their main difference lies in

- how the **prompt is constructed**, and
- how the information is **propagated/shared** among agents.
"""
import asyncio
import json
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.memory import InMemoryMemory
from agentscope.formatter import (
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
)
from agentscope.model import DashScopeChatModel
from agentscope.message import Msg
from agentscope.pipeline import MsgHub
from agentscope.tool import Toolkit

# %%
# User-Agent Conversation
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# User-agent conversation, also known as chatbot, is the most common usage
# scenario of LLM-empowered agents, and the design target of most LLM APIs.
# Such conversation features only two participants: a user and an agent.
#
# In AgentScope, the formatters with **"Chat"** in its name are designed for
# user-agent conversation, such as ``DashScopeChatFormatter``,
# ``AnthropicChatFormatter``, etc.
# They use the ``role`` field in the message to distinguish the user and the
# agent, and format the messages accordingly.
#
# Here we build a simple conversation between agent ``Friday`` and user.
#
# .. tip:: AgentScope provides a built-in ``UserAgent`` class for human-in-the-loop (HITL) interaction. Refer to :ref:`user-agent` for more details.
#

friday = ReActAgent(
    name="Friday",
    sys_prompt="You're a helpful assistant named Friday",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),  # The formatter for user-agent conversation
    memory=InMemoryMemory(),
    toolkit=Toolkit(),
)

# Create a user agent
user = UserAgent(name="User")

# %%
# Now, we can program the conversation by exchanging messages between these two agents until the user types "exit" to end the conversation.
#
# .. code-block:: python
#
#     async def run_conversation() -> None:
#         """Run a simple conversation between Friday and User."""
#         msg = None
#         while True:
#             msg = await friday(msg)
#             msg = await user(msg)
#             if msg.get_text_content() == "exit":
#                 break
#
#     asyncio.run(run_conversation())
#

# %%
# More than Two Agents
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# As stated in the beginning, we demonstrate how to build conversation with multiple agents in terms of **prompt construction** and **information sharing**.
#
# Prompt Construction
# -------------------------------
# In AgentScope, we provide built-in formatters for multi-agent conversation, featuring **"MultiAgent"** in their names, such as ``DashScopeMultiAgentFormatter``, ``AnthropicMultiAgentFormatter``, etc.
#
# Specifically, they use the ``name`` field in the message to distinguish different agents, and format the conversation history into a single user message.
# Taking ``DashScopeMultiAgentFormatter`` as an example:
#
# .. tip:: More details about the formatter can be found in :ref:`prompt`.
#


async def example_multi_agent_prompt() -> None:
    msgs = [
        Msg("system", "You're a helpful assistant named Bob.", "system"),
        Msg("Alice", "Hi!", "user"),
        Msg("Bob", "Hi! Nice to meet you guys.", "assistant"),
        Msg("Charlie", "Me too! I'm Charlie, by the way.", "assistant"),
    ]

    formatter = DashScopeMultiAgentFormatter()
    prompt = await formatter.format(msgs)

    print("Formatted prompt:")
    print(json.dumps(prompt, indent=4, ensure_ascii=False))

    # We print the content of the combined user message here for better
    # understanding:
    print("-------------")
    print("Combined message")
    print(prompt[1]["content"])


asyncio.run(example_multi_agent_prompt())


# %%
# Message Sharing
# -------------------------------
# In multi-agent conversation, exchanging messages explicitly may not be efficient and convenient, especially when broadcasting messages among multiple agents.
#
# Therefore, AgentScope provides an async context manager named ``MsgHub`` to simplify the operation of broadcasting messages.
# Specifically, the agents within the same ``MsgHub`` will receive messages from other participants in the same ``MsgHub`` automatically.
#

model = DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
)
formatter = DashScopeMultiAgentFormatter()

alice = ReActAgent(
    name="Alice",
    sys_prompt="You're a student named Alice.",
    model=model,
    formatter=formatter,
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
)

bob = ReActAgent(
    name="Bob",
    sys_prompt="You're a student named Bob.",
    model=model,
    formatter=formatter,
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
)

charlie = ReActAgent(
    name="Charlie",
    sys_prompt="You're a student named Charlie.",
    model=model,
    formatter=formatter,
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
)


async def example_msghub() -> None:
    """Example of using MsgHub for multi-agent conversation."""
    async with MsgHub(
        [alice, bob, charlie],
        announcement=Msg(
            "system",
            "Now you meet each other with a brief self-introduction.",
            "system",
        ),
    ):
        await alice()
        await bob()
        await charlie()


asyncio.run(example_msghub())

# %%
# Now we print the memory of Alice to check if her memory is updated correctly.
#


async def example_memory() -> None:
    """Print the memory of Alice."""
    print("Memory of Alice:")
    for msg in await alice.memory.get_memory():
        print(f"{msg.name}: {msg.get_text_content()}")


asyncio.run(example_memory())

# %%
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`prompt`
# - :ref:`pipeline`
#
