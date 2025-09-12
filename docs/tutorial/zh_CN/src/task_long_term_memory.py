# -*- coding: utf-8 -*-
"""
.. _long-term-memory:

长期记忆
========================

AgentScope 为长期记忆提供了一个基类 ``LongTermMemoryBase`` 和一个基于 `mem0 <https://github.com/mem0ai/mem0>`_ 的具体实现 ``Mem0LongTermMemory``。
结合 :ref:`agent` 章节中 ``ReActAgent`` 类的设计，我们提供了两种长期记忆模式：

- ``agent_control``：智能体通过工具调用自主管理长期记忆。
- ``static_control``：开发者通过编程显式控制长期记忆操作。

当然，开发者也可以使用 ``both`` 参数，将同时激活上述两种记忆管理模式。

.. hint:: 不同的记忆模式适用于不同的使用场景，开发者可以根据需要选择合适的模式。

使用 mem0 长期记忆
~~~~~~~~~~~~~~~~~~~~~~~~

.. note:: 在 GitHub 仓库的 ``examples/long_term_memory/mem0`` 目录下我们提供了 mem0 长期记忆的使用示例。

"""

import os
import asyncio

from agentscope.message import Msg
from agentscope.memory import InMemoryMemory, Mem0LongTermMemory
from agentscope.agent import ReActAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


# 创建 mem0 长期记忆实例
long_term_memory = Mem0LongTermMemory(
    agent_name="Friday",
    user_name="user_123",
    model=DashScopeChatModel(
        model_name="qwen-max-latest",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
    ),
    embedding_model=DashScopeTextEmbedding(
        model_name="text-embedding-v2",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
    ),
    on_disk=False,
)

# %%
# ``Mem0LongTermMemory`` 类提供了两个操作长期记忆的方法，``record` 和 `retrieve`。
# 它们接收消息对象的列表作为输入，分别记录和检索长期记忆中的信息。
#
# 例如下面的例子中，我们先存入用户的一条偏好，然后在长期记忆中检索相关信息。
#


# 基本使用示例
async def basic_usage():
    """基本使用示例"""
    # 记录记忆
    await long_term_memory.record([Msg("user", "我喜欢住民宿", "user")])

    # 检索记忆
    results = await long_term_memory.retrieve(
        [Msg("user", "我的住宿偏好", "user")],
    )
    print(f"检索结果: {results}")


asyncio.run(basic_usage())


# %%
# 与 ReAct 智能体集成
# ----------------------------------------
# AgentScope 中的 ``ReActAgent`` 在构造函数中包含 ``long_term_memory`` 和 ``long_term_memory_mode`` 两个参数，
# 其中 ``long_term_memory`` 用于指定长期记忆实例，``long_term_memory_mode`` 的取值为 ``"agent_control"``, ``"static_control"`` 或 ``"both"``。
#
# 当 ``long_term_memory_mode`` 设置为 ``"agent_control"`` 或 ``both`` 时，在 ``ReActAgent`` 的构造函数中将
# 注册两个工具函数：``record_to_memory`` 和 ``retrieve_from_memory``。
# 从而使智能体能够自主的管理长期记忆。
#
# .. note:: 为了达到最好的效果，``"agent_control"`` 模式可能还需要在系统提示（system prompt）中添加相应的说明。
#

# 创建带有长期记忆的 ReAct 智能体
agent = ReActAgent(
    name="Friday",
    sys_prompt="你是一个具有长期记忆功能的助手。",
    model=DashScopeChatModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        model_name="qwen-max-latest",
    ),
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="static_control",  # 使用 static_control 模式
)


async def record_preferences():
    """ReAct agent integration example"""
    # 对话示例
    msg = Msg("user", "我去杭州旅行时，喜欢住民宿", "user")
    await agent(msg)


asyncio.run(record_preferences())

# %%
# 然后我们清空智能体的短期记忆，以避免造成干扰，并测试智能体是否会记住之前的对话。
#


async def retrieve_preferences():
    """Retrieve user preferences from long-term memory"""
    # 我们清空智能体的短期记忆，以避免造成干扰
    await agent.memory.clear()

    # 测试智能体是否会记住之前的对话
    msg2 = Msg("user", "我有什么偏好？简要的回答我", "user")
    await agent(msg2)


asyncio.run(retrieve_preferences())

# %%
# 自定义长期记忆
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 提供了 ``LongTermMemoryBase`` 基类，它定义了长期记忆的基本接口。
#
# 开发者可以继承 ``LongTermMemoryBase`` 并实现以下的抽象方法来定义自己的长期记忆类：
#
# .. list-table:: AgentScope 中的长期记忆类
#     :header-rows: 1
#
#     * - 类
#       - 抽象方法
#       - 描述
#     * - ``LongTermMemoryBase``
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - - 如果想支持 “static_control” 模式，必须实现 ``record`` 和 ``retrieve`` 方法。
#         - 想要支持 “agent_control” 模式，必须实现 ``record_to_memory`` 和 ``retrieve_from_memory`` 方法。
#     * - ``Mem0LongTermMemory``
#       - \-
#       - 基于 mem0 库的长期记忆实现，支持向量存储和检索。
#
#
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`memory` - 基础记忆系统
# - :ref:`agent` - ReAct 智能体
# - :ref:`tool` - 工具系统
