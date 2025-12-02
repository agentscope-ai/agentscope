# -*- coding: utf-8 -*-
"""
.. _builtin_agent:

内置智能体
=============================

AgentScope 内置若干智能体类，以支持不同使用场景，同时展示如何使用 AgentScope 构建智能体。

.. list-table::
    :header-rows: 1

    * - 类
      - 描述
    * - `UserAgent`
      - 允许用户参与对话的智能体。
    * - `DialogAgent`
      - 使用自然语言交谈的智能体。
    * - `DictDialogAgent`
      - 支持结构化输出的智能体。
    * - `ReActAgent`
      - 以 reasoning-acting 循环的方式使用工具的智能体。
    * - `LlamaIndexAgent`
      - 检索增强型生成 (RAG) 智能体。

"""

import agentscope

for module in agentscope.agents.__all__:
    if module.endswith("Agent"):
        print(module)

# %%
# .. note:: 为了使同一个智能体类能够支持不同的大语言模型 API，所有内置智能体类都通过模型配置名 `model_config_name` 来进行初始化。如果你构建的智能体不打算多个不同的模型，推荐可以显式地进行模型初始化，而不是使用模型配置名。
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
# DialogAgent 是 AgentScope 中最基本的智能体，可以以对话的方式与用户交互。
# 开发者可以通过提供不同的系统提示和模型配置来自定义它。
#

from agentscope.agents import DialogAgent
from agentscope.message import Msg

# 初始化一个对话智能体
alice = DialogAgent(
    name="Alice",
    model_config_name="my-qwen-max",
    sys_prompt="你是一个名叫 Alice 的助手。",
)

# 向智能体发送一条消息
msg = Msg("Bob", "嗨！你叫什么名字？", "user")
response = alice(msg)

# %%
# UserAgent
# ----------------------------
# `UserAgent` 类允许用户与其他智能体交互。
# 当调用 `UserAgent` 对象时，它会要求用户输入，并将其格式化为 `Msg` 对象。
#
# 这里我们展示如何初始化一个 `UserAgent` 对象，并与对话智能体 `alice` 进行交互。
#

from agentscope.agents import UserAgent
from io import StringIO
import sys

user = UserAgent(
    name="Bob",
    input_hint="用户输入: \n",
)

# 模拟用户输入
sys.stdin = StringIO("你认识我吗？\n")

msg = user()
msg = alice(msg)

# %%
# DictDialogAgent
# ----------------------------
# `DictDialogAgent` 支持结构化输出，并可通过 `set_parser` 方法指定解析器来实现自动后处理。
#
# 我们首先初始化一个 `DictDialogAgent` 对象，然后通过更换解析器，实现不同结构化的输出。
#

from agentscope.agents import DictDialogAgent
from agentscope.parsers import MarkdownJsonDictParser


charles = DictDialogAgent(
    name="Charles",
    model_config_name="my-qwen-max",
    sys_prompt="你是一个名叫 Charles 的助手。",
    max_retries=3,  # 获取所需结构化输出失败时的最大重试次数
)

# 要求智能体生成包含 `thought`、`speak` 和 `decision` 的结构化输出
parser1 = MarkdownJsonDictParser(
    content_hint={
        "thought": "你的想法",
        "speak": "你要说的话",
        "decision": "你的最终决定，true/false",
    },
    keys_to_metadata="decision",
    required_keys=["thought", "speak", "decision"],
)

charles.set_parser(parser1)
msg1 = charles(Msg("Bob", "在下雨天外出是个好主意吗？", "user"))

print(f"metadata字段: {msg1.metadata}")
print(f"metadata字段的类型: {type(msg1.metadata)}")

# %%
# 然后，我们要求智能体从 1 到 10 中选择一个数字。
#

parser2 = MarkdownJsonDictParser(
    content_hint={
        "thought": "你的想法",
        "speak": "你要说的话",
        "number": "你选择的数字",
    },
    keys_to_metadata="number",
    keys_to_content="speak",
)

charles.set_parser(parser2)
msg2 = charles(Msg("Bob", "从 1 到 10 中选择一个数字。", "user"))

print(f"响应消息的content字段: {msg2.content}")
print(f"响应消息的metadata字段: {msg2.metadata}")

# %%
# 下一个问题是如何对结构化输出进行后处理。
# 例如，`thought` 字段应该存储在记忆中而不暴露给其他人，
# 而 `speak` 字段应该显示给用户，`decision` 字段应该能够在响应消息对象中轻松访问。
#

parser3 = MarkdownJsonDictParser(
    content_hint={
        "thought": "你的想法",
        "speak": "你要说的话",
        "number": "你选择的数字",
    },
    required_keys=["thought", "speak", "number"],
    keys_to_memory=["thought", "speak", "number"],  # 需要存储在记忆中
    keys_to_content="speak",  # 需要显示给用户
    keys_to_metadata="number",  # 需要存储在响应消息的元数据中
)

charles.set_parser(parser3)

msg3 = charles(Msg("Bob", "从 20 到 30 中选择一个数字。", "user"))

print(f"内容字段: {msg3.content}")
print(f"内容字段的类型: {type(msg3.content)}\n")

print(f"元数据字段: {msg3.metadata}")
print(f"元数据字段的类型: {type(msg3.metadata)}")


# %%
# .. hint:: 有关结构化输出的高级用法和更多不同解析器，请参阅 :ref:`structured-output` 章节。
#
# ReActAgent
# ----------------------------
# `ReActAgent` 以 reasoning-acting 循环的方式使用工具来解决给定的问题。
#
# 首先我们为智能体准备一个工具函数。
#

from agentscope.service import ServiceToolkit, execute_python_code


toolkit = ServiceToolkit()

# 通过指定部分参数将 execute_python_code 设置为工具，这里用户需要在 add 方法里面配置部分
# 参数，通常是一些应该由开发者提供的参数，例如 API Key 等，剩余参数由智能体自己填写。
toolkit.add(
    execute_python_code,
    timeout=300,
    use_docker=False,
    maximum_memory_bytes=None,
)

# %%
# 然后我们初始化一个 `ReActAgent` 来解决给定的问题。
#

from agentscope.agents import ReActAgent

david = ReActAgent(
    name="David",
    model_config_name="my-qwen-max",
    sys_prompt="你是一个名叫 David 的助手。",
    service_toolkit=toolkit,
    max_iters=10,
    verbose=True,
)

task = Msg("Bob", "请帮我计算 151513434*54353453453。", "user")

response = david(task)


# %%
# LlamaIndexAgent
# ----------------------------
# 有关更多详细信息，请参阅检索增强型生成 (RAG) 章节。
#
