# -*- coding: utf-8 -*-
"""
.. _build-agent:

构建智能体
====================

AgentScope 中，可以通过继承基类`agentscope.agents.AgentBase`来构建智能体

在下面，我们将构建一个简单的，可以和其他人互动的智能体。

"""

from agentscope.agents import AgentBase
from agentscope.memory import TemporaryMemory
from agentscope.message import Msg
from agentscope.models import DashScopeChatWrapper
import json


# %%
# 定义智能体
# --------------------------------
# 继承 `agentscope.agents.AgentBase` 类并实现其构造函数和 `reply` 方法。
#
# 在构造函数中，我们初始化智能体的名字、系统提示、记忆模块和模型。
# 在本例中，我们采用 DashScope Chat API 中的 `qwen-max` 作为模型服务。
# 当然，你可以将其替换为 `agentscope.models` 下的其它模型。
#
# `reply`方法是智能体的核心，它接受消息作为输入并返回回复消息。
# 在该方法中，我们实现了智能体的基本逻辑:
#
# - 在记忆中记录输入消息，
# - 使用系统提示和记忆构建提示词，
# - 调用模型获取返回值，
# - 在记忆中记录返回值并返回一个消息。
#


class JarvisAgent(AgentBase):
    def __init__(self):
        super().__init__("Jarvis")

        self.name = "Jarvis"
        self.sys_prompt = "你是一个名为Jarvis的助手。"
        self.memory = TemporaryMemory()
        self.model = DashScopeChatWrapper(
            config_name="_",
            model_name="qwen-max",
        )

    def reply(self, msg):
        # 在记忆中记录消息
        self.memory.add(msg)

        # 使用系统提示和记忆构建上下文
        prompt = self.model.format(
            Msg(
                name="system",
                content=self.sys_prompt,
                role="system",
            ),
            self.memory.get_memory(),
        )

        # 调用模型获取响应
        response = self.model(prompt)

        # 在记忆中记录响应消息并返回
        msg = Msg(
            name=self.name,
            content=response.text,
            role="assistant",
        )
        self.memory.add(msg)

        self.speak(msg)
        return msg


# %%
# 创建智能体类后，我们实例化它，并通过发送消息与之交互。
#

jarvis = JarvisAgent()

msg = Msg(
    name="user",
    content="嗨！Jarvis。",
    role="user",
)

msg_reply = jarvis(msg)

print(f"消息的发送者: {msg_reply.name}")
print(f"发送者的角色: {msg_reply.role}")
print(f"消息的内容: {msg_reply.content}")


# %%
# ======================
#
# 组件
# ----------
# 现在我们简要介绍上述智能体中使用到的基本组件，包括
#
# * 记忆
# * 模型
#
# 记忆
# ^^^^^^^
# 记忆模块提供了记忆管理的基本操作。
#

memory = TemporaryMemory()

# 添加一条消息
memory.add(Msg("system", "你是一个名为Jarvis的助手。", "system"))

# 一次添加多条消息
memory.add(
    [
        Msg("Stank", "嗨!", "user"),
        Msg("Jarvis", "我能为您做些什么吗?", "assistant"),
    ],
)

print(f"当前记忆: {memory.get_memory()}")
print(f"当前大小: {memory.size()}")

# %%
# 使用参数 `recent_n` 获取最后两条消息。
#

recent_two_msgs = memory.get_memory(recent_n=2)

for i, msg in enumerate(recent_two_msgs):
    print(
        f"MSG{i}: 发送者: {msg.name}, 角色: {msg.role}, 内容: {msg.content}",
    )

# %%
# 删除记忆中的第一条消息。
#

memory.delete(0)

print(f"删除后的记忆: {memory.get_memory()}")
print(f"删除后的大小: {memory.size()}")

# %%
# 模型
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# `agentscope.models` 封装了不同的模型 API，并在其 `format` 函数中为不同的 API 提供了基本的提示词构建策略。
#
# 以 DashScope Chat API 为例:
#

messages = [
    Msg("system", "你是一个名为Jarvis的助手。", "system"),
    Msg("Stank", "嗨!", "user"),
    Msg("Jarvis", "我能为您做些什么吗?", "assistant"),
]

model = DashScopeChatWrapper(
    config_name="api",
    model_name="qwen-max",
)
prompt = model.format(messages)
print(json.dumps(prompt, indent=4, ensure_ascii=False))

# %%
#
# 进一步阅读
# ---------------------
# - :ref:`builtin-agent`
# - :ref:`model_api`
