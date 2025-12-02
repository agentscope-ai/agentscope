# -*- coding: utf-8 -*-
"""
.. _build-agent:

Build Agent
====================

AgentScope supports to build LLM-empowered agents by providing a basic agent
class `agentscope.agents.AgentBase`.

In the following, we will build a simple dialog agent that can interact with
the others.

"""

from agentscope.agents import AgentBase
from agentscope.memory import TemporaryMemory
from agentscope.message import Msg
from agentscope.models import DashScopeChatWrapper
import json


# %%
# Define the Agent
# --------------------------------
# We define a `DialogAgent` class by inheriting from
# `agentscope.agents.AgentBase`, and implement the constructor and
# `reply` methods to make the agent work.
#
# Within the constructor, we initialize the agent with its name, system prompt,
# memory, and model.
# In this example, we take `qwen-max` in DashScope Chat API for model serving.
# You can replace it with other model wrappers under `agentscope.models`.
#
# The `reply` method is the core of the agent, which takes message(s) as input
# and returns a reply message.
# Within the method, we implement the basic logic of the agent:
# - record the input message in memory,
# - construct the prompt with system prompt and memory,
# - call the model to get the response,
# - record the response in memory and return it.
#


class JarvisAgent(AgentBase):
    def __init__(self):
        super().__init__("Jarvis")

        self.name = "Jarvis"
        self.sys_prompt = "You're a helpful assistant named Jarvis."
        self.memory = TemporaryMemory()
        self.model = DashScopeChatWrapper(
            config_name="_",
            model_name="qwen-max",
        )

    def reply(self, msg):
        # Record the message in memory
        self.memory.add(msg)

        # Construct the prompt with system prompt and memory
        prompt = self.model.format(
            Msg(
                name="system",
                content=self.sys_prompt,
                role="system",
            ),
            self.memory.get_memory(),
        )

        # Call the model to get the response
        response = self.model(prompt)

        # Record the response in memory and return it
        msg = Msg(
            name=self.name,
            content=response.text,
            role="assistant",
        )
        self.memory.add(msg)

        self.speak(msg)
        return msg


# %%
# After creating the agent class, we can instantiate it and interact with it
# by sending messages.
#

jarvis = JarvisAgent()

msg = Msg(
    name="user",
    content="Hi! Jarvis.",
    role="user",
)

msg_reply = jarvis(msg)

print(f"The sender name of the replied message: {msg_reply.name}")
print(f"The role of the sender: {msg_reply.role}")
print(f"The content of the replied message: {msg_reply.content}")


# %%
# ======================
#
# Components
# ----------
# Now we briefly introduce the basic components of the above agent, including
#
# * memory
# * model
#
# Memory
# ^^^^^^^
# The [memory module](#memory) provides basic operations for memory
# management, including adding, deleting and getting memory.
#

memory = TemporaryMemory()
# Add a message
memory.add(Msg("system", "You're a helpful assistant named Jarvis.", "system"))
# Add multiple messages at once
memory.add(
    [
        Msg("Stank", "Hi!", "user"),
        Msg("Jarvis", "How can I help you?", "assistant"),
    ],
)
print(f"The current memory: {memory.get_memory()}")
print(f"The current size: {memory.size()}")

# %%
# Obtain the last two messages with parameter `recent_n`.
#

recent_two_msgs = memory.get_memory(recent_n=2)
for i, msg in enumerate(recent_two_msgs):
    print(
        f"MSG{i}: Sender: {msg.name}, Role: {msg.role}, Content: {msg.content}",
    )

# %%
# Delete the first message within the memory.
memory.delete(0)
print(f"The memory after deletion: {memory.get_memory()}")
print(f"The size after deletion: {memory.size()}")

# %%
# Model
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# The `agentscope.models` module encapsulates different model API, and
# provides basic prompt engineering strategies for different APIs in their
# `format` function.
#
# Take DashScope Chat API as an example:
#

messages = [
    Msg("system", "You're a helpful assistant named Jarvis.", "system"),
    Msg("Stank", "Hi!", "user"),
    Msg("Jarvis", "How can I help you?", "assistant"),
]

model = DashScopeChatWrapper(
    config_name="api",
    model_name="qwen-max",
)
prompt = model.format(messages)
print(json.dumps(prompt, indent=4))

# %%
#
# Further Reading
# ---------------------
# - :ref:`builtin_agent`
# - :ref:`model_api`
