# -*- coding: utf-8 -*-
"""
.. _conversation:

Conversation
======================

Conversation 是一种智能体间交换和共享信息的设计模式，常见于游戏、聊天机器人和多智能体讨论场景。

在 AgentScope 中，conversation 的构建在 **显式的消息传递** 基础上。在本章中，我们将演示如何构建：

- User-assistant 之间的对话（聊天机器人）
- 多实体对话（游戏、讨论等）

它们的主要区别在于

- **提示的构建方式**，以及
- 信息在智能体之间的 **传播/共享** 方式。
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
# User-Assistant 对话
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# User-assistant 对话，也称为聊天机器人（chatbot），是最常见的智能体应用，也是当前大多数 LLM API 的设计模式。
# 在这种对话只有两个参与者：用户（user）和智能体（assistant）。
#
# 在 AgentScope 中，名称中带有 **"Chat"** 的格式化器专为 user-assistant 对话设计，
# 如 ``DashScopeChatFormatter``、``AnthropicChatFormatter`` 等。
# 它们使用消息中的 ``role`` 字段来区分用户和智能体，并相应地格式化消息。
#
# 这里我们构建智能体 ``Friday`` 和用户之间的简单对话。
#
# .. tip:: AgentScope 提供了内置的 ``UserAgent`` 类，用于人机交互（HITL）。更多详细信息请参考 :ref:`user-agent`。
#

friday = ReActAgent(
    name="Friday",
    sys_prompt="你是一个名为 Friday 的有用助手",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),  # 用于 user-assistant 对话的格式化器
    memory=InMemoryMemory(),
    toolkit=Toolkit(),
)

# 创建用户智能体
user = UserAgent(name="User")

# %%
# 现在，我们可以通过在这两个智能体之间交换消息来开始对话，直到用户输入"exit"结束对话。
#
# .. code-block:: python
#
#     async def run_conversation() -> None:
#         """运行 Friday 和用户之间的简单对话。"""
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
# 多实体对话
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 如开头所述，我们演示如何在 **提示构建** 和 **信息共享** 方面构建多智能体对话。
#
# 构建提示
# -------------------------------
# 在 AgentScope 中，我们为多智能体对话提供了内置格式化器，其名称中带有 **"MultiAgent"**，
# 如 ``DashScopeMultiAgentFormatter``、``AnthropicMultiAgentFormatter`` 等。
#
# 具体而言，它们使用消息中的 ``name`` 字段来区分不同的实体，并将对话历史格式化为单个用户消息。
# 以 ``DashScopeMultiAgentFormatter`` 为例：
#
# .. tip:: 有关格式化器的更多详细信息可以在 :ref:`prompt` 中找到。
#


async def example_multi_agent_prompt() -> None:
    msgs = [
        Msg("system", "你是一个名为 Bob 的有用助手。", "system"),
        Msg("Alice", "嗨！", "user"),
        Msg("Bob", "嗨！很高兴见到大家。", "assistant"),
        Msg("Charlie", "我也是！顺便说一下，我是 Charlie。", "assistant"),
    ]

    formatter = DashScopeMultiAgentFormatter()
    prompt = await formatter.format(msgs)

    print("格式化的提示：")
    print(json.dumps(prompt, indent=4, ensure_ascii=False))

    # 我们在这里打印组合用户消息的内容以便更好地理解：
    print("-------------")
    print("组合消息")
    print(prompt[1]["content"])


asyncio.run(example_multi_agent_prompt())


# %%
# 消息共享
# -------------------------------
# 在多智能体对话中，显式交换消息可能不够高效和便利，
# 特别是在多个智能体之间广播消息时。
#
# 因此，AgentScope 提供了一个名为 ``MsgHub`` 的异步上下文管理器来简化消息广播。
# 具体而言，同一个 ``MsgHub`` 中的智能体将自动接收其它参与者通过 ``reply`` 函数返回的消息。
#
# 下面我们构建一个多人聊天的场景，多个智能体扮演不同的角色：
#

model = DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
)
formatter = DashScopeMultiAgentFormatter()

alice = ReActAgent(
    name="Alice",
    sys_prompt="你是一个名为 Alice 的学生。",
    model=model,
    formatter=formatter,
)

bob = ReActAgent(
    name="Bob",
    sys_prompt="你是一个名为 Bob 的学生。",
    model=model,
    formatter=formatter,
)

charlie = ReActAgent(
    name="Charlie",
    sys_prompt="你是一个名为 Charlie 的学生。",
    model=model,
    formatter=formatter,
)


async def example_msghub() -> None:
    """使用 MsgHub 进行多智能体对话的示例。"""
    async with MsgHub(
        [alice, bob, charlie],
        # 进入 MsgHub 时的公告消息
        announcement=Msg(
            "system",
            "现在大家互相认识一下，简单自我介绍。",
            "system",
        ),
    ):
        await alice()
        await bob()
        await charlie()


asyncio.run(example_msghub())

# %%
# 现在我们打印 Alice 的记忆，检查她的记忆是否正确更新。
#


async def example_memory() -> None:
    """打印 Alice 的记忆。"""
    print("Alice 的记忆：")
    for msg in await alice.memory.get_memory():
        print(
            f"{msg.name}: {json.dumps(msg.content, indent=4, ensure_ascii=False)}",
        )


asyncio.run(example_memory())

# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`prompt`
# - :ref:`pipeline`
#
