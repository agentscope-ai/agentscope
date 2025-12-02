# -*- coding: utf-8 -*-
"""
.. _build-conversation:

构建对话
======================

AgentScope 中，不同智能体之间通过“显式的消息交换”来构建对话。

"""

from agentscope.agents import DialogAgent, UserAgent
from agentscope.message import Msg
from agentscope import msghub
import agentscope

# 为简单起见，通过模型配置进行初始化
agentscope.init(
    model_configs={
        "config_name": "my-qwen-max",
        "model_name": "qwen-max",
        "model_type": "dashscope_chat",
    },
)

# %%
# 两个智能体
# -----------------------------
# 这里我们构建一个简单的对话，对话双方是两个智能体 `Angel` 和 `Monster`。
#

angel = DialogAgent(
    name="Angel",
    sys_prompt="你是一个名叫 Angel 的歌手，说话风格简单凝练。",
    model_config_name="my-qwen-max",
)

monster = DialogAgent(
    name="Monster",
    sys_prompt="你是一个名叫 Monster 的运动员，说话风格简单凝练。",
    model_config_name="my-qwen-max",
)

# %%
# 现在，我们让这两个智能体对话三轮。
#

msg = None
for _ in range(3):
    msg = angel(msg)
    msg = monster(msg)

# %%
# 如果你想参与到对话中，只需实例化一个内置的 `UserAgent` 来向智能体输入消息。
#

user = UserAgent(name="User")

# %%
# 多于两个智能体
# ---------------------
# 当一个对话中有多于两个智能体时，来自一个智能体的消息应该广播给所有其他智能体。
#
# 为了简化广播消息的操作，AgentScope 提供了一个 `msghub` 模块。
# 具体来说，在同一个 `msghub` 中的智能体会自动接收其它参与者发送的消息。
# 我们只需要组织发言的顺序，而不需要显式地将消息传递给其它智能体。
#
# 当然，你也可以显式传递消息，但是记忆模块中的查重操作会自动跳过添加重复的消息。因此不会造成记忆中的消息重复。
#
# 这里是一个 `msghub` 的示例，我们首先创建三个智能体：Alice、Bob 和 Charlie，它们都使用` qwen-max` 模型。

alice = DialogAgent(
    name="Alice",
    sys_prompt="你是一个名叫Alice的助手。",
    model_config_name="my-qwen-max",
)

bob = DialogAgent(
    name="Bob",
    sys_prompt="你是一个名叫Bob的助手。",
    model_config_name="my-qwen-max",
)

charlie = DialogAgent(
    name="Charlie",
    sys_prompt="你是一个名叫Charlie的助手。",
    model_config_name="my-qwen-max",
)

# %%
# 三个智能体将在对话中轮流报数。
#

# 介绍对话规则
greeting = Msg(
    name="user",
    content="现在开始从1开始逐个报数，每次只产生一个数字，绝对不能产生其他任何信息。",
    role="user",
)

with msghub(
    participants=[alice, bob, charlie],
    announcement=greeting,  # 开始时，通知消息将广播给所有参与者。
) as hub:
    # 对话的第一轮
    alice()
    bob()
    charlie()

    # 我们可以动态管理参与者，例如从对话中删除一个智能体
    hub.delete(charlie)

    # 向所有参与者广播一条消息
    hub.broadcast(
        Msg(
            "user",
            "Charlie已离开对话。",
            "user",
        ),
    )

    # 对话的第二轮
    alice()
    bob()
    charlie()

# %%
# 现在我们打印Alice和Bob的记忆，以检查操作是否成功。

print("Alice的记忆：")
for msg in alice.memory.get_memory():
    print(f"{msg.name}：{msg.content}")

print("\nCharlie的记忆：")
for msg in charlie.memory.get_memory():
    print(f"{msg.name}：{msg.content}")

# %%
# 在上面的示例中，Charlie 在第一轮结束后离开了对话，因此没有收到 Alice 和 Bob 的"4"和"5"。
# 所以第二轮时它报了"4"。
# 另一方面，Alice 和 Bob 继续了对话，没有 Charlie 的参与。
#
