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
    * - 支持记忆压缩
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
# 记忆压缩
# ----------------------------------------
# 随着对话的不断增长，记忆中的 token 数量可能会超过模型的上下文限制或导致推理速度变慢。
# ``ReActAgent`` 提供了自动记忆压缩功能来解决这个问题。
#
# **基础用法**
#
# 要启用记忆压缩，在初始化 ``ReActAgent`` 时提供一个 ``CompressionConfig`` 实例：
#
# .. code-block:: python
#
#     from agentscope.agent import ReActAgent
#     from agentscope.token import CharTokenCounter
#
#     agent = ReActAgent(
#         name="助手",
#         sys_prompt="你是一个有用的助手。",
#         model=model,
#         formatter=formatter,
#         compression_config=ReActAgent.CompressionConfig(
#             enable=True,
#             agent_token_counter=CharTokenCounter(),  # 智能体的 token 计数器
#             trigger_threshold=10000,  # 超过 10000 个 token 时触发压缩
#             keep_recent=3,            # 保持最近 3 条消息不被压缩
#         ),
#     )
#
# 启用记忆压缩后，智能体会监控其记忆中的 token 数量。
# 一旦超过 ``trigger_threshold``，智能体会自动：
#
# 1. 识别尚未被压缩的消息（通过 ``exclude_mark``）
# 2. 保持最近 ``keep_recent`` 条消息不被压缩（以保留最近的上下文）
# 3. 将较早的消息发送给 LLM 生成结构化摘要
# 4. 使用 ``MemoryMark.COMPRESSED`` 标记已压缩的消息（通过 ``update_messages_mark``）
# 5. 将摘要存储在记忆中（通过 ``update_compressed_summary``）
#
# .. important:: 压缩采用**标记机制**而非替换消息。旧消息被标记为已压缩，并通过 ``exclude_mark=MemoryMark.COMPRESSED`` 在后续检索中被排除，而生成的摘要则单独存储，在需要时检索。这种方式保留了原始消息，允许灵活的记忆管理。关于标记功能的更多详情，请参考 :ref:`memory`。
#
# 默认情况下，压缩摘要被结构化为五个关键字段：
#
# - **task_overview**：用户的核心请求和成功标准
# - **current_state**：到目前为止已完成的工作，包括文件和输出
# - **important_discoveries**：技术约束、决策、错误和失败的尝试
# - **next_steps**：完成任务所需的具体操作
# - **context_to_preserve**：用户偏好、领域细节和做出的承诺
#
# **自定义压缩**
#
# 可以通过指定 ``summary_schema``、``summary_template`` 和 ``compression_prompt`` 参数来自定义压缩的工作方式。
#
# - **summary_schema**：使用 Pydantic 模型定义压缩摘要的结构
# - **compression_prompt**：指导 LLM 如何生成摘要
# - **summary_template**：格式化压缩摘要如何呈现给智能体
#
# 下面是一个自定义压缩的示例：
#
# .. code-block:: python
#
#     from pydantic import BaseModel, Field
#
#     # 定义自定义摘要结构
#     class CustomSummary(BaseModel):
#         main_topic: str = Field(
#             max_length=200,
#             description="对话的主题"
#         )
#         key_points: str = Field(
#             max_length=400,
#             description="讨论的重要观点"
#         )
#         pending_tasks: str = Field(
#             max_length=200,
#             description="待完成的任务"
#         )
#
#     # 使用自定义压缩配置创建智能体
#     agent = ReActAgent(
#         name="助手",
#         sys_prompt="你是一个有用的助手。",
#         model=model,
#         formatter=formatter,
#         compression_config=ReActAgent.CompressionConfig(
#             enable=True,
#             agent_token_counter=CharTokenCounter(),
#             trigger_threshold=10000,
#             keep_recent=3,
#             # 结构化摘要的自定义 schema
#             summary_schema=CustomSummary,
#             # 指导压缩的自定义提示
#             compression_prompt=(
#                 "<system-hint>请总结上述对话，"
#                 "重点关注主题、关键讨论点和待完成任务。</system-hint>"
#             ),
#             # 格式化摘要的自定义模板
#             summary_template=(
#                 "<system-info>对话摘要：\n"
#                 "主题：{main_topic}\n\n"
#                 "关键观点：\n{key_points}\n\n"
#                 "待完成任务：\n{pending_tasks}"
#                 "</system-info>"
#             ),
#         ),
#     )
#
# ``summary_template`` 使用 ``summary_schema`` 中定义的字段作为占位符
# （例如 ``{main_topic}``、``{key_points}``）。在 LLM 生成结构化摘要后，
# 这些占位符将被实际值替换。
#
# .. note:: 智能体确保工具使用和工具结果对在压缩过程中保持在一起，以维护对话流程的完整性。
#
# .. tip:: 可以通过指定不同的 ``compression_model`` 和 ``compression_formatter`` 来使用更小、更快的模型进行压缩，以降低成本和延迟。
#
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
#     * - ``A2aAgent``
#       - \-
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#       - 用于与远程 A2A 代理通信的智能体，详见 :ref:`a2a`
#
#
#
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`tool`
# - :ref:`hook`
# - :ref:`a2a`
#
