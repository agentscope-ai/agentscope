# -*- coding: utf-8 -*-
"""
.. _pipeline:

管道 (Pipeline)
========================

对于多智能体编排，AgentScope 提供了 ``agentscope.pipeline`` 模块
作为将智能体链接在一起的语法糖，具体包括

- **MsgHub**: 用于多个智能体之间消息的广播
- **sequential_pipeline** 和 **SequentialPipeline**: 以顺序方式执行多个智能体的函数式和类式实现
- **fanout_pipeline** 和 **FanoutPipeline**: 将相同输入分发给多个智能体的函数式和类式实现
- **stream_printing_messages**: 将智能体在回复过程中，调用 ``self.print`` 打印的消息转换为一个异步生成器

"""

import os, asyncio

from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub, stream_printing_messages


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


asyncio.run(check_broadcast_message())

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
# 目前，AgentScope 提供三种管道，用于减轻开发者的负担：
#
# 1. **顺序管道 (Sequential Pipeline)**: 按预定义顺序逐个执行智能体
# 2. **扇出管道 (Fanout Pipeline)**: 将相同输入分发给多个智能体并收集它们的响应
# 3. **流式获取打印消息 (stream printing messages)**: 将智能体在回复过程中，调用 ``self.print`` 打印的消息转换为一个异步生成器
#
# 顺序管道
# ------------------------
# 顺序管道逐个执行智能体，前一个智能体的输出成为下一个智能体的输入。
#
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

# %%
# 扇出管道
# ------------------------
# 扇出管道将相同的输入消息同时分发给多个智能体并收集所有响应。当你想要收集对同一话题的不同观点或专业意见时，这非常有用。
#
# 例如，以下两个代码片段是等价的：
#
# .. code-block:: python
#     :caption: 代码片段 3: 手动逐个调用智能体
#
#     from copy import deepcopy
#
#     msgs = []
#     msg = None
#     for agent in [alice, bob, charlie, david]:
#         msgs.append(await agent(deepcopy(msg)))
#
#
# .. code-block:: python
#     :caption: 代码片段 4: 使用扇出管道
#
#     from agentscope.pipeline import fanout_pipeline
#
#     msgs = await fanout_pipeline(
#         # 要执行的智能体列表
#         agents=[alice, bob, charlie, david],
#         # 输入消息，可以是 None
#         msg=None,
#         enable_gather=False,
#     )
#
# .. note::
#     ``enable_gather`` 参数控制扇出管道的执行模式：
#
#     - ``enable_gather=True`` (默认): 使用 ``asyncio.gather()`` **并发** 执行所有智能体。这为 I/O 密集型操作（如 API 调用）提供更好的性能，因为智能体并行运行。
#     - ``enable_gather=False``: 逐个 **顺序** 执行智能体。当你需要确定性的执行顺序或想要避免并发请求压垮外部服务时，这很有用。
#
#     选择并发执行以获得更好的性能，或选择顺序执行以获得可预测的顺序和资源控制。
#
# .. tip::
#     通过结合 ``MsgHub`` 和 ``sequential_pipeline`` 或 ``fanout_pipeline``，你可以非常容易地创建更复杂的工作流。

# %%
# 流式获取打印消息
# ------------------------
# ``stream_printing_messages`` 函数将智能体在回复过程中调用 ``self.print`` 打印的消息转换为一个异步生成器。
# 可以帮助开发者快速以流式方式获取智能体的中间消息。
#
# 该函数接受一个或多个智能体和一个协程任务作为输入，并返回一个异步生成器。
# 该异步生成器返回一个二元组，包含执行协程任务过程中通过 ``await self.print(...)`` 打印的消息，以及一个布尔值，表示该消息是否为一组流式消息中的最后一个。
#
# 需要注意的是，生成器返回的元组中，布尔值表示该消息是否为一组流式消息中的最后一个，而非此次智能体调用的最后一条消息。


async def run_example_pipeline() -> None:
    """运行流式打印消息的示例。"""
    agent = create_agent("Alice", 20, "student")

    # 我们关闭agent的终端打印以避免输出混乱
    agent.set_console_output_enabled(False)

    async for msg, last in stream_printing_messages(
        agents=[agent],
        coroutine_task=agent(
            Msg("user", "你好，你是谁？", "user"),
        ),
    ):
        print(msg, last)
        if last:
            print()


asyncio.run(run_example_pipeline())


# %%
# 高级管道特性
# ~~~~~~~~~~~~~~~~~~~~~~~~~
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
# .. code-block:: python
#     :caption: 使用 FanoutPipeline 类
#
#     from agentscope.pipeline import FanoutPipeline
#
#     # 创建管道对象
#     pipeline = FanoutPipeline(agents=[alice, bob, charlie, david])
#
#     # 调用管道
#     msgs = await pipeline(msg=None)
#
#     # 使用不同输入复用管道
#     msgs = await pipeline(msg=Msg("user", "你好！", "user"))
#
