# -*- coding: utf-8 -*-
"""
.. _agent:

智能体
=========================

在章我们首先重点介绍 AgentScope 中的 ReAct 智能体，然后简要介绍如何从零开始自定义智能体。

ReAct 智能体
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

在 AgentScope 中，``ReActAgent`` 类将各种功能集成到最终实现中，具体包括

.. list-table:: ``ReActAgent`` 的功能特性
    :header-rows: 1

    * - 功能特性
      - 参考文档
    * - 支持实时介入（Realtime Steering）
      -
    * - 支持并行工具调用
      -
    * - 支持结构化输出
      -
    * - 支持智能体自主管理工具（Meta tool）
      - :ref:`tool`
    * - 支持函数粒度的 MCP 控制
      - :ref:`mcp`
    * - 支持智能体自主控制长期记忆
      - :ref:`long-term-memory`
    * - 支持自动状态管理
      - :ref:`state`


由于篇幅限制，本章我们仅演示 ``ReActAgent`` 类的前三个功能特性，其它功能我们在对应的章节进行介绍。

"""

import asyncio
import json
import os
from datetime import datetime
import time

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import TextBlock, Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse


# %%
# 实时控制
# ---------------------------------------
#
# 实时控制指 **允许用户随时中断智能体的回复，介入智能体的执行过程**，AgentScope 基于 asyncio 取消机制实现了该功能。
#
# 具体来说，AgentScope 中智能体提供了 ``interrupt`` 方法，当该函数被调用时，它将取消当前正在执行的 `reply` 函数，并执行 ``handle_interrupt`` 方法进行后处理。
#
# .. hint:: 结合 :ref:`tool` 中提到的 AgentScope 支持工具函数流式返回结果的功能，工具执行过程中如果执行时间过长或偏离用户期望，用户可以通过在终端中按 Ctrl+C 或在代码中调用智能体的
#  ``interrupt`` 方法来中断工具执行。
#
# .. hint:: ``ReActAgent`` 中提供了完善的中断逻辑，智能体的记忆和状态会在中断发生时被正确的保存。
#
# 中断逻辑已在 ``AgentBase`` 类中作为基本功能实现，并提供 ``handle_interrupt`` 抽象方法供用户自定义
# 中断的后处理，如下所示：
#
# .. code-block:: python
#
#     # AgentBase 的代码片段
#     class AgentBase:
#         ...
#         async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
#             ...
#             reply_msg: Msg | None = None
#             try:
#                 self._reply_task = asyncio.current_task()
#                 reply_msg = await self.reply(*args, **kwargs)
#
#             except asyncio.CancelledError:
#                 # 捕获中断并通过 handle_interrupt 方法处理
#                 reply_msg = await self.handle_interrupt(*args, **kwargs)
#
#             ...
#
#         @abstractmethod
#         async def handle_interrupt(self, *args: Any, **kwargs: Any) -> Msg:
#             pass
#
#
# 在 ``ReActAgent`` 类的实现中，我们返回一个固定消息"I noticed that you have interrupted me. What can I do for you?"，如下所示：
#
# .. figure:: ../../_static/images/interruption_zh.gif
#     :width: 100%
#     :align: center
#     :class: bordered-image
#     :alt: 中断示例
#
#     中断智能体 ``reply`` 的执行过程
#
# 开发者可以通过覆盖 ``handle_interrupt`` 函数实现自定义的中断后处理逻辑，例如，调用 LLM 生成对中断的简单响应。
#
#
# 并行工具调用
# ----------------------------------------
# ``ReActAgent`` 通过在其构造函数中提供 ``parallel_tool_calls`` 参数来支持并行工具调用。
# 当 LLM 生成多个工具调用且 ``parallel_tool_calls`` 设置为 ``True`` 时，
# 它们将通过 ``asyncio.gather`` 函数并行执行。
#
# .. note:: ``ReActAgent`` 中的工具并行调用是基于异步 ``asyncio.gather`` 实现的，因此，只有当工具函数是异步函数，同时工具函数内也为异步逻辑时，才能最大程度发挥工具并行执行的效果
#
# .. note:: 运行时请确保模型层面支持工具并行调用，并且相应参数设置正确（可以通过 ``generate_kwargs`` 传入），例如对于DashScope API，需要设置 ``parallel_tool_calls`` 为 ``True``，否则将无法进行并行工具调用。


# 准备一个工具函数
async def example_tool_function(tag: str) -> ToolResponse:
    """一个示例工具函数"""
    start_time = datetime.now().strftime("%H:%M:%S.%f")

    # 休眠 3 秒以模拟长时间运行的任务
    await asyncio.sleep(3)

    end_time = datetime.now().strftime("%H:%M:%S.%f")
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"标签 {tag} 开始于 {start_time}，结束于 {end_time}。",
            ),
        ],
    )


toolkit = Toolkit()
toolkit.register_tool_function(example_tool_function)

# 创建一个 ReAct 智能体
agent = ReActAgent(
    name="Jarvis",
    sys_prompt="你是一个名为 Jarvis 的有用助手。",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        # 启用并行工具调用
        generate_kwargs={
            "parallel_tool_calls": True,
        },
    ),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    parallel_tool_calls=True,
)


async def example_parallel_tool_calls() -> None:
    """并行工具调用示例"""
    # 提示智能体同时生成两个工具调用
    await agent(
        Msg(
            "user",
            "同时生成两个 'example_tool_function' 函数的工具调用，标签分别为 'tag1' 和 'tag2'，以便它们可以并行执行。",
            "user",
        ),
    )


asyncio.run(example_parallel_tool_calls())

# %%
# 结构化输出
# ----------------------------------------
# AgentScope 中的结构化输出是与工具调用紧密结合的。具体来说，``ReActAgent`` 类在其 ``__call__`` 函数中接收 ``pydantic.BaseModel`` 的子类作为 ``structured_model`` 参数。
# 从而提供复杂的结构化输出限制。
# 然后我们可以从 返回消息的 ``metadata`` 字段获取结构化输出。
#
# 以介绍爱因斯坦为例：
#

# 创建一个 ReAct 智能体
agent = ReActAgent(
    name="Jarvis",
    sys_prompt="你是一个名为 Jarvis 的有用助手。",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),
)


# 结构化模型
class Model(BaseModel):
    name: str = Field(description="人物的姓名")
    description: str = Field(description="人物的一句话描述")
    age: int = Field(description="年龄")
    honor: list[str] = Field(description="人物荣誉列表")


async def example_structured_output() -> None:
    """结构化输出示例"""
    res = await agent(
        Msg(
            "user",
            "介绍爱因斯坦",
            "user",
        ),
        structured_model=Model,
    )
    print("\n结构化输出：")
    print(json.dumps(res.metadata, indent=4, ensure_ascii=False))


asyncio.run(example_structured_output())

# %%
# 自定义智能体
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 提供了两个基类：``AgentBase`` 和 ``ReActAgentBase``，它们在抽象方法和支持的钩子函数方面有所不同。
# 具体来说，``ReActAgentBase`` 扩展了 ``AgentBase``，增加了额外的 ``_reasoning`` 和 ``_acting`` 抽象方法，以及它们的前置和后置钩子函数。
#
# 开发者可以根据需要选择继承这两个基类中的任一个。
# 我们总结了 ``agentscope.agent`` 模块下的智能体如下：
#
# .. list-table:: AgentScope 中的智能体类
#     :header-rows: 1
#
#     * - 类
#       - 抽象方法
#       - 支持的钩子函数
#       - 描述
#     * - ``AgentBase``
#       - | ``reply``
#         | ``observe``
#         | ``print``
#         | ``handle_interrupt``
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#       - 所有智能体的基类，提供基本接口和钩子。
#     * - ``ReActAgentBase``
#       - | ``reply``
#         | ``observe``
#         | ``print``
#         | ``handle_interrupt``
#         | ``_reasoning``
#         | ``_acting``
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#         | pre\_/post_reasoning
#         | pre\_/post_acting
#       - ReAct 类智能体的抽象类，扩展了 ``AgentBase``，增加了 ``_reasoning`` 和 ``_acting`` 抽象方法及其钩子。
#     * - ``ReActAgent``
#       - \-
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#         | pre\_/post_reasoning
#         | pre\_/post_acting
#       - ``ReActAgentBase`` 的实现
#     * - ``UserAgent``
#       -
#       -
#       - 代表用户的特殊智能体，用于与智能体交互
#
#
#
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`tool`
# - :ref:`hook`
#
