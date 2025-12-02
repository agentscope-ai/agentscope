# -*- coding: utf-8 -*-
"""
.. _build-conversation:

Build Conversation
======================

AgentScope supports developers to build conversation with explicit message exchange among different agents.
"""

from agentscope.agents import DialogAgent, UserAgent
from agentscope.message import Msg
from agentscope import msghub
import agentscope

# Initialize via model configuration for simplicity
agentscope.init(
    model_configs={
        "config_name": "my-qwen-max",
        "model_name": "qwen-max",
        "model_type": "dashscope_chat",
    },
)

# %%
# Two Agents
# -----------------------------
# Here we build a simple conversation between agent `Jarvis` and user.

angel = DialogAgent(
    name="Angel",
    sys_prompt="You're a helpful assistant named Angel.",
    model_config_name="my-qwen-max",
)

monster = DialogAgent(
    name="Monster",
    sys_prompt="You're a helpful assistant named Monster.",
    model_config_name="my-qwen-max",
)

# %%
# Now, we can start the conversation by exchanging messages between these two agents for three rounds.

msg = None
for _ in range(3):
    msg = angel(msg)
    msg = monster(msg)

# %%
# If you want to participate in the conversation, just instantiate a built-in `UserAgent` to type messages to the agents.

user = UserAgent(name="User")

# %%
# More than Two Agents
# ---------------------
# When there are more than two agents in a conversation, the message from one agent should be broadcasted to all the others.
#
# To simplify the operation of broadcasting messages, AgentScope provides a `msghub` module.
# Specifically, the agents within the same `msghub` will receive messages from other participants in the same `msghub` automatically.
# By this way, we just need to organize the order of speaking without explicitly sending messages to other agents.
#
# Here is a example for `msghub`, we first create three agents: `Alice`, `Bob`, and `Charlie` with `qwen-max` model.

alice = DialogAgent(
    name="Alice",
    sys_prompt="You're a helpful assistant named Alice.",
    model_config_name="my-qwen-max",
)

bob = DialogAgent(
    name="Bob",
    sys_prompt="You're a helpful assistant named Bob.",
    model_config_name="my-qwen-max",
)

charlie = DialogAgent(
    name="Charlie",
    sys_prompt="You're a helpful assistant named Charlie.",
    model_config_name="my-qwen-max",
)

# %%
# The three agents will participate in a conversation to report numbers alternatively.

# Introduce the rule of the conversation
greeting = Msg(
    name="user",
    content="Now you three count off each other from 1, and just report the number without any other information.",
    role="user",
)

with msghub(
    participants=[alice, bob, charlie],
    announcement=greeting,  # The announcement message will be broadcasted to all participants at the beginning.
) as hub:
    # The first round of the conversation
    alice()
    bob()
    charlie()

    # We can manage the participants dynamically, e.g. delete an agent from the conversation.
    hub.delete(charlie)

    # Broadcast a message to all participants
    hub.broadcast(
        Msg(
            "user",
            "Charlie has left the conversation.",
            "user",
        ),
    )

    # The second round of the conversation
    alice()
    bob()
    charlie()

# %%
# Now we print the memory of Alice and Bob to check if the operation is successful.

print("Memory of Alice:")
for msg in alice.memory.get_memory():
    print(f"{msg.name}: {msg.content}")

print("\nMemory of Charlie:")
for msg in charlie.memory.get_memory():
    print(f"{msg.name}: {msg.content}")

# %%
# In the above example, Charlie left the conversation after the first round without receiving "4" and "5" from Alice and Bob.
# Therefore, it reported "4" in the second round.
# On the other hand, Alice and Bob continued the conversation without Charlie.
