# -*- coding: utf-8 -*-
"""
.. _builtin-agent

Built-in Agents
=============================

AgentScope builds in several agent class to support different scenarios and
show how to build agents with AgentScope.

.. list-table::
    :header-rows: 1

    * - Class
      - Description
    * - `UserAgent`
      - Agent that allows user to participate in the conversation.
    * - `DialogAgent`
      - Agent that speaks in natural language.
    * - `DictDialogAgent`
      - Agent that supports structured output.
    * - `ReActAgent`
      - Agent that uses tools in a reasoning-acting loop manner.
    * - `LlamaIndexAgent`
      - RAG agent.

"""

import agentscope

for module in agentscope.agents.__all__:
    if module.endswith("Agent"):
        print(module)

# %%
# .. note:: To support different LLM APIs, all built-in agents are initialized by specifying the model configuration name `model_config_name` in AgentScope.
#

import agentscope

agentscope.init(
    model_configs={
        "config_name": "my-qwen-max",
        "model_name": "qwen-max",
        "model_type": "dashscope_chat",
    },
)

# %%
# DialogAgent
# ----------------------------
# The dialog agent is the most basic agent in AgentScope, which can interact
# with users in a dialog manner.
# Developers can customize it by providing different system prompts and model
# configurations.
#

from agentscope.agents import DialogAgent
from agentscope.message import Msg

# Init a dialog agent
alice = DialogAgent(
    name="Alice",
    model_config_name="my-qwen-max",
    sys_prompt="You're a helpful assistant named Alice.",
)

# Send a message to the agent
msg = Msg("Bob", "Hi! What's your name?", "user")
response = alice(msg)

# %%
# UserAgent
# ----------------------------
# The `UserAgent` class allows users to interact with the other agents.
# When the `UserAgent` object is called, it will ask for user input and format
# it into a `Msg` object.
#
# Here we show how to initialize a `UserAgent` object and interact with the
# dialog agent `alice`.
#

from agentscope.agents import UserAgent
from io import StringIO
import sys

user = UserAgent(
    name="Bob",
    input_hint="User input: \n",
)

# Simulate user input
sys.stdin = StringIO("Do you know me?\n")

msg = user()
msg = alice(msg)

# %%
# DictDialogAgent
# ----------------------------
# The `DictDialogAgent` supports structured output and automatic post-processing by specifying its parser via the `set_parser` method.
#
#
# We first initialize a `DictDialogAgent` object, and switch between different outputs by changing the parser.
#

from agentscope.agents import DictDialogAgent
from agentscope.parsers import MarkdownJsonDictParser


charles = DictDialogAgent(
    name="Charles",
    model_config_name="my-qwen-max",
    sys_prompt="You're a helpful assistant named Charles.",
    max_retries=3,  # The maximum number of retries when failing to get a required structured output
)

# Ask the agent to generate structured output with `thought`, `speak`, and `decision`
parser1 = MarkdownJsonDictParser(
    content_hint={
        "thought": "what your thought",
        "speak": "what you speak",
        "decision": "your final decision, true/false",
    },
    keys_to_metadata="decision",
    required_keys=["thought", "speak", "decision"],
)

charles.set_parser(parser1)
msg1 = charles(Msg("Bob", "Is it a good idea to go out in the rain?", "user"))

print(f"The metadata field: {msg1.metadata}")
print(f"The type of metadata field: {type(msg1.metadata)}")

# %%
# Then, we ask the agent to pick a number from 1 to 10.

parser2 = MarkdownJsonDictParser(
    content_hint={
        "thought": "what your thought",
        "speak": "what you speak",
        "number": "the number you choose",
    },
    keys_to_metadata="number",
    keys_to_content="speak",
)

charles.set_parser(parser2)
msg2 = charles(Msg("Bob", "Pick a number from 1 to 10.", "user"))

print(f"The content of the response message: {msg2.content}")
print(f"The metadata of the response message: {msg2.metadata}")

# %%
# The next question is how to post-process the structured output.
# For example, the `thought` field should be stored in memory without being exposed to the others,
# while the `speak` field should be displayed to the user, and the `decision` field should be easily accessible in the response message object.
#

parser3 = MarkdownJsonDictParser(
    content_hint={
        "thought": "what your thought",
        "speak": "what you speak",
        "number": "The number you choose",
    },
    required_keys=["thought", "speak", "number"],
    keys_to_memory=["thought", "speak", "number"],  # to be stored in memory
    keys_to_content="speak",  # to be displayed
    keys_to_metadata="number",  # to be stored in metadata field of the response message
)

charles.set_parser(parser3)

msg3 = charles(Msg("Bob", "Pick a number from 20 to 30.", "user"))

print(f"The content field: {msg3.content}")
print(f"The type of content field: {type(msg3.content)}\n")

print(f"The metadata field: {msg3.metadata}")
print(f"The type of metadata field: {type(msg3.metadata)}")


# %%
# .. hint:: More advanced usage of structured output, and more different parsers refer to the section :ref:`structured-output`.
#
# ReActAgent
# ----------------------------
# The `ReActAgent` uses tools to solve the given problem in a reasoning-acting
# loop manner.
#
# First we prepare a tool function for the agent.
#

from agentscope.service import ServiceToolkit, execute_python_code


toolkit = ServiceToolkit()
# Set execute_python_code as a tool by specifying partial arguments
toolkit.add(
    execute_python_code,
    timeout=300,
    use_docker=False,
    maximum_memory_bytes=None,
)

# %%
# Then we initialize a `ReActAgent` to solve the given problem.
#

from agentscope.agents import ReActAgent

david = ReActAgent(
    name="David",
    model_config_name="my-qwen-max",
    sys_prompt="You're a helpful assistant named David.",
    service_toolkit=toolkit,
    max_iters=10,
    verbose=True,
)

task = Msg("Bob", "Help me to calculate 151513434*54353453453.", "user")

response = david(task)


# %%
# LlamaIndexAgent
# ----------------------------
# Refer to the RAG Section for more details.
#
