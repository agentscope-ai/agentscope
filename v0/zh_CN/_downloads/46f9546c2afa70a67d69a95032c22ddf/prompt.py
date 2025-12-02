# -*- coding: utf-8 -*-
"""
.. _prompt-engineering:

Prompt 格式化
================================

智能体应用中，重要的一点是构建符合模型 API 要求的输入（prompt）。AgentScope 中，我们为开
发者提供了一些列的内置策略，以支持不同的模型 API 和场景（chat 和 multi-agent）。

AgentScope 支持特定模型的 prompt 格式化，也支持模型未知的格式化。

.. tip:: **Chat 场景** 只涉及到 user 和 assistant 两个角色；而 **multi-agent**
场景会涉及到多个智能体，它们的角色（role）虽然都是 assistant，但是指向不同的实体。

.. note:: 目前，多数的大语言模型 API 服务只支持 chat 场景。例如，对话只涉及到两个角色
（user 和 assistant），部分 API 还要求它们必须交替发送消息。

.. note:: 目前还没有一种提示工程可以做到一劳永逸。AgentScope 内置提示构建策略的目标
 是让初学者可以顺利调用模型 API,而不是达到最佳性能。
 对于高级用户，我们建议开发人员根据需求和模型 API 要求来自定义提示构建策略。

模型未知的格式化
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

当我们需要应用能够在不同的模型 API 上都能运行的时候，我们需要进行模型未知的 prompt 格式化。

AgentScope 通过支持从配置中加载模型，并在 model wrapper 类中内置了一系列不同的格式化策略
来实现模型未知的格式化。同时支持 chat 和 multi-agent 场景。

开发者可以直接使用 model wrapper 对象的 `format` 方法来格式化输入消息，而无需了解模型
 API 的细节。以 DashScope Chat API 为例：

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

# 从 ModelManager 中获取模型对象
model = ModelManager.get_instance().get_model_by_config_name("my_qwen")

# 可以将 `Msg` 对象或 `Msg` 对象列表传递给 `format` 方法
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
# 格式化输入消息后，我们可以将其传给 `model` 对象，进行实际的 API 调用。
#

response = model(prompt)

print(response.text)

# %%
# 同样，我们可以通过设置 `multi_agent_mode=True` 在 multi-agent 场景下格式化消息。

prompt = model.format(
    Msg("system", "你是一个名为Alice的AI助手，你会与其他人进行交流", "system"),
    [
        Msg("Alice", "Hi!", "assistant"),
        Msg("Bob", "Nice to meet you!", "assistant"),
    ],
    multi_agent_mode=True,
)

print(json.dumps(prompt, indent=4, ensure_ascii=False))

# %%
# 在 AgentScope 的智能体类中，模型未知的格式化实现如下：


class MyAgent(AgentBase):
    def __init__(self, name: str, model_config_name: str, **kwargs) -> None:
        super().__init__(name=name, model_config_name=model_config_name)

        # ...

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        # ...

        # 在模型类型未知的情况下，可以直接进行格式化
        prompt = self.model.format(
            Msg("system", "{your system prompt}", "system"),
            self.memory.get_memory(),
            multi_agent_mode=True,
        )
        response = self.model(prompt)

        # ...
        return Msg(self.name, response.text, role="assistant")


# %%
# .. tip:: Model wrapper 类的格式化功能全部实现在 `agentscope.formatter` 模块中。
# Model wrapper 类会根据模型名字来决定使用哪一种格式化策略。

# %%
# 模型已知的格式化
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# `agentscope.formatter` 模块中实现了一系列的格式化策略，以支持不同的模型 API 和场景。
# 具体而言，开发者可以调用 `format_chat` 和 `format_multi_agent` 方法来格式化 chat 和
# multi-agent 场景下的消息。同时，还提供了一个 `format_auto` 方法，他会自动根据输入
# 消息中涉及到的角色实体数量来决定使用哪种格式化策略。


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
# Multi-agent 场景:

formatted_multi_agent = OpenAIFormatter.format_multi_agent(
    multi_agent_messages,
)
print(json.dumps(formatted_multi_agent, indent=4, ensure_ascii=False))

# %%
# Chat 场景:

formatted_chat = OpenAIFormatter.format_chat(
    chat_messages,
)
print(json.dumps(formatted_chat, indent=4, ensure_ascii=False))

# %%
# 自动格式化（输入中只包含 user 和 assistant 两个实体）：

formatted_auto_chat = OpenAIFormatter.format_auto(
    chat_messages,
)
print(json.dumps(formatted_auto_chat, indent=4, ensure_ascii=False))

# %%
# 自动格式化（输入中包含多个实体，即 multi-agent）：

formatted_auto_multi_agent = OpenAIFormatter.format_auto(
    multi_agent_messages,
)
print(json.dumps(formatted_auto_multi_agent, indent=4, ensure_ascii=False))

# %%
# AgentScope 中可用的 formatter 类如下：

from agentscope.formatters import (
    CommonFormatter,
    AnthropicFormatter,
    OpenAIFormatter,
    GeminiFormatter,
    DashScopeFormatter,
)


# %%
# `CommonFormatter` 是用于一般 chat LLMs 的基本格式化器，
# 例如 ZhipuAI API、Yi API、ollama、LiteLLM 等。
#
# 视觉模型
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# 对于视觉模型，AgentScope 目前支持 OpenAI，Dashscope 和 Anthropic API。
#

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
