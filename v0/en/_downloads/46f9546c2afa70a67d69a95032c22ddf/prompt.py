# -*- coding: utf-8 -*-
"""
.. _prompt-engineering:

Prompt Formatting
================================

AgentScope supports developers to build prompt that fits different model APIs
by providing a set of built-in strategies for both chat and multi-agent
scenarios.

Specifically, AgentScope supports both model-specific and model-agnostic
formatting.

.. tip:: **Chat scenario** refers to the conversation between a user and an
 assistant, while **multi-agent scenario** involves multiple agents with
 different names (though their roles are all "assistant").

.. note:: Currently, most LLM API providers only support chat scenario. For
 example, only two roles (user and assistant) are involved in the conversation
 and sometimes they must speak alternatively.

.. note:: There is no **one-size-fits-all** solution for prompt formatting.
 The goal of built-in strategies is to **enable beginners to smoothly invoke
 the model API, rather than achieving the best performance**.
 For advanced users, we highly recommend developers to customize prompts
 according to their needs and model API requirements.

Model-Agnostic Formatting
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When you want your application to work with different model APIs
simultaneously, the model-agnostic formatting is a good idea.

AgentScope achieves model-agnostic formatting by supporting to load the model
from configuration, and presets a collection of built-in formatting strategies
for different model APIs and scenarios (chat or multi-agent) in the
model wrapper class.

You can directly use the `format` method of the model object to format the
input messages without knowing the details of the model API. Taking DashScope
Chat API as an example:

"""
from typing import Union, Optional

from agentscope.agents import AgentBase
from agentscope.message import Msg
from agentscope.manager import ModelManager
import agentscope
import json

# Load the model configuration
agentscope.init(
    model_configs={
        "config_name": "my_qwen",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    },
)

# Get the model object from model manager
model = ModelManager.get_instance().get_model_by_config_name("my_qwen")

# `Msg` objects or a list of `Msg` objects can be passed to the `format` method
prompt = model.format(
    Msg("system", "You're a helpful assistant.", "system"),
    [
        Msg("assistant", "Hi!", "assistant"),
        Msg("user", "Nice to meet you!", "user"),
    ],
    multi_agent_mode=False,
)

print(json.dumps(prompt, indent=4, ensure_ascii=False))

# %%
# After formatting the input messages, we can input the prompt into the model
# object.

response = model(prompt)

print(response.text)

# %%
# Also, you can use format the messages in the multi-agent scenario by
# setting `multi_agent_mode=True`.

prompt = model.format(
    Msg("system", "You're a helpful assistant named Alice.", "system"),
    [
        Msg("Alice", "Hi!", "assistant"),
        Msg("Bob", "Nice to meet you!", "assistant"),
    ],
    multi_agent_mode=True,
)

print(json.dumps(prompt, indent=4, ensure_ascii=False))

# %%
# Within the agent, the model-agnostic formatting is achieved as follows:


class MyAgent(AgentBase):
    def __init__(self, name: str, model_config_name: str, **kwargs) -> None:
        super().__init__(name=name, model_config_name=model_config_name)

        # ...

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        # ...

        # Format the messages without knowing the model API
        prompt = self.model.format(
            Msg("system", "{your system prompt}", "system"),
            self.memory.get_memory(),
            multi_agent_mode=True,
        )
        response = self.model(prompt)

        # ...
        return Msg(self.name, response.text, role="assistant")


# %%
# .. tip:: All the formatting strategies are implemented under
# `agentscope.formatter` module. The model wrapper decides which strategy to
# use based on the model name.

# %%
# Model-Specific Formatting
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# The `agentscope.formatter` module implements the built-in formatting
# strategies for different model APIs and scenarios. They provide
# `format_chat` and `format_multi_agent` methods, as well as a `format_auto`
# function that automatically selects the appropriate method based on the
# input messages.

from agentscope.formatters import OpenAIFormatter

multi_agent_messages = [
    Msg("system", "You're a helpful assistant named Alice.", "system"),
    Msg("Alice", "Hi!", "assistant"),
    Msg("Bob", "Nice to meet you!", "assistant"),
    Msg("Charlie", "Nice to meet you, too!", "user"),
]

chat_messages = [
    Msg("system", "You're a helpful assistant named Alice.", "system"),
    Msg("Bob", "Nice to meet you!", "user"),
    Msg("Alice", "Hi! How can I help you?", "assistant"),
]


# %%
# Multi-agent scenario:

formatted_multi_agent = OpenAIFormatter.format_multi_agent(
    multi_agent_messages,
)
print(json.dumps(formatted_multi_agent, indent=4, ensure_ascii=False))

# %%
# Chat scenario:

formatted_chat = OpenAIFormatter.format_chat(
    chat_messages,
)
print(json.dumps(formatted_chat, indent=4, ensure_ascii=False))

# %%
# Auto formatting when only two entities are involved:

formatted_auto_chat = OpenAIFormatter.format_auto(
    chat_messages,
)
print(json.dumps(formatted_auto_chat, indent=4, ensure_ascii=False))

# %%
# Auto formatting when more than two entities (multi-agent) are involved:

formatted_auto_multi_agent = OpenAIFormatter.format_auto(
    multi_agent_messages,
)
print(json.dumps(formatted_auto_multi_agent, indent=4, ensure_ascii=False))

# %%
# The available formatter classes are:

from agentscope.formatters import (
    CommonFormatter,
    AnthropicFormatter,
    OpenAIFormatter,
    GeminiFormatter,
    DashScopeFormatter,
)

# %%
# The `CommonFormatter` is a basic formatter for common chat LLMs, such as
# ZhipuAI API, Yi API, ollama, LiteLLM, etc.
#
# Vision Models
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# For vision models, AgentScope currently supports OpenAI, DashScope and
# Anthropic vision models.

from agentscope.message import TextBlock, ImageBlock

# we create a fake image locally
with open("./image.jpg", "w") as f:
    f.write("fake image")

multi_modal_messages = [
    Msg("system", "You're a helpful assistant named Alice.", "system"),
    Msg(
        "Alice",
        [
            TextBlock(type="text", text="Help me to describe the two images?"),
            ImageBlock(type="image", url="https://example.com/image.jpg"),
            ImageBlock(type="image", url="./image.jpg"),
        ],
        "user",
    ),
    Msg("Bob", "Sure!", "assistant"),
]

# %%
print("OpenAI prompt:")
openai_prompt = OpenAIFormatter.format_chat(multi_modal_messages)
print(json.dumps(openai_prompt, indent=4, ensure_ascii=False))

# %%
#
print("\nDashscope prompt:")
dashscope_prompt = DashScopeFormatter.format_chat(multi_modal_messages)
print(json.dumps(dashscope_prompt, indent=4, ensure_ascii=False))

# %%
#
print("\nAnthropic prompt:")
anthropic_prompt = AnthropicFormatter.format_chat(multi_modal_messages)
print(json.dumps(anthropic_prompt, indent=4, ensure_ascii=False))
