# -*- coding: utf-8 -*-
"""
.. _pipeline:

管道 (Pipeline)
========================

对于多智能体编排，AgentScope 提供了 ``agentscope.pipeline`` 模块
作为将智能体链接在一起的语法糖，具体包括

- **MsgHub**: 用于多个智能体之间消息的广播
- **sequential_pipeline** 和 **SequentialPipeline**: 以顺序方式执行多个智能体

"""

import os, asyncio

from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub

# %%
# 使用 MsgHub 进行广播
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``MsgHub`` 类是一个 **异步上下文管理器**，它接收一个智能体列表作为其参与者。
# 当一个参与者生成回复消息时，将通过调用所有其他参与者的 ``observe`` 方法广播该消息。
# 这意味着在 ``MsgHub`` 上下文中，开发者无需手动将回复消息从一个智能体发送到另一个智能体。
#
# 这里我们创建四个智能体：Alice、Bob、Charlie 和 David。
# 然后我们让 Alice、Bob 和 Charlie 通过自我介绍开始一个会议。需要注意的是 David 没有包含在这个会议中。
#


def create_agent(name: str, age: int, career: str) -> ReActAgent:
    """根据给定信息创建智能体对象。"""
    return ReActAgent(
        name=name,
        sys_prompt=f"你是{name}，一个{age}岁的{career}",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )


alice = create_agent("Alice", 50, "老师")
bob = create_agent("Bob", 35, "工程师")
charlie = create_agent("Charlie", 28, "设计师")
david = create_agent("David", 30, "开发者")

# %%
# 然后我们创建一个 ``MsgHub`` 上下文，并让他们自我介绍:
#
# .. hint:: ``announcement`` 中的消息将在进入 ``MsgHub`` 上下文时广播给所有参与者。
#


async def example_broadcast_message():
    """使用 MsgHub 广播消息的示例。"""

    # 创建消息中心
    async with MsgHub(
        participants=[alice, bob, charlie],
        announcement=Msg(
            "user",
            "现在请简要介绍一下自己，包括你的姓名、年龄和职业。",
            "user",
        ),
    ) as hub:
        # 无需手动消息传递的群聊
        await alice()
        await bob()
        await charlie()


asyncio.run(example_broadcast_message())

# %%
# 现在让我们检查 Bob、Charlie 和 David 是否收到了 Alice 的消息。
#


async def check_broadcast_message():
    """检查消息是否正确广播。"""
    user_msg = Msg(
        "user",
        "你知道 Alice 是谁吗，她是做什么的？",
        "user",
    )

    await bob(user_msg)
    await charlie(user_msg)
    await david(user_msg)


# %%
# 现在我们观察到 Bob 和 Charlie 知道 Alice 和她的职业，而 David 对
# Alice 一无所知，因为他没有包含在 ``MsgHub`` 上下文中。
#
#
# 动态管理
# ---------------------------
# 此外，``MsgHub`` 支持通过以下方法动态管理参与者：
#
# - ``add``: 添加一个或多个智能体作为新参与者
# - ``delete``: 从参与者中移除一个或多个智能体，他们将不再接收广播消息
# - ``broadcast``: 向所有当前参与者广播消息
#
# .. note:: 新添加的参与者不会接收到之前的消息。
#
# .. code-block:: python
#
#       async with MsgHub(participants=[alice]) as hub:
#           # 添加新参与者
#           hub.add(david)
#
#           # 移除参与者
#           hub.delete(alice)
#
#           # 向所有当前参与者广播
#           await hub.broadcast(
#               Msg("system", "现在我们开始...", "system"),
#           )
#
#
# 管道
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 管道是 AgentScope 中多智能体编排的一种语法糖。
#
# 目前，AgentScope 提供了一个顺序管道实现，它按预定义的顺序逐个执行智能体。
# 例如，以下两个代码片段是等价的：
#
# .. code-block:: python
#     :caption: 代码片段 1: 手动消息传递
#
#     msg = None
#     msg = await alice(msg)
#     msg = await bob(msg)
#     msg = await charlie(msg)
#     msg = await david(msg)
#
#
# .. code-block:: python
#     :caption: 代码片段 2: 使用顺序管道
#
#     from agentscope.pipeline import sequential_pipeline
#
#     msg = await sequential_pipeline(
#         # 按顺序执行的智能体列表
#         agents=[alice, bob, charlie, david],
#         # 第一个输入消息，可以是 None
#         msg=None
#     )
#
# .. tip:: 通过结合 `MsgHub` 和 `sequential_pipeline`，你可以非常容易地创建更复杂的工作流。
#
#
# 此外，为了可重用性，我们还提供了基于类的实现：
#
# .. code-block:: python
#    :caption: 使用 SequentialPipeline 类
#
#     from agentscope.pipeline import SequentialPipeline
#
#     # 创建管道对象
#     pipeline = SequentialPipeline(agents=[alice, bob, charlie, david])
#
#     # 调用管道
#     msg = await pipeline(msg=None)
#
#     # 使用不同输入复用管道
#     msg = await pipeline(msg=Msg("user", "你好！", "user"))
#
#
