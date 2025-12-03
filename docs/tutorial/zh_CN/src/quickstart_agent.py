# -*- coding: utf-8 -*-
"""
.. _react-agent:

创建 ReAct 智能体
====================

AgentScope 在 ``agentscope.agent`` 模块下提供了开箱即用的 ReAct 智能体 ``ReActAgent`` 供开发者使用。

它同时支持以下功能：

- ✨ 基础功能
    - 支持围绕 ``reply``、``observe``、``print``、``_reasoning`` 和 ``_acting`` 的 **钩子函数（hooks）**
    - 支持结构化输出
- ✋ 实时介入（Realtime Steering）
    - 支持用户 **中断**
    - 支持自定义 **中断处理**
- 🛠️ 工具
    - 支持 **同步/异步** 工具函数
    - 支持 **流式** 工具响应
    - 支持 **状态化** 的工具管理
    - 支持 **并行** 工具调用
    - 支持 **MCP** 服务器
- 💾 记忆
    - 支持智能体 **自主管理** 长期记忆
    - 支持“静态”的长期记忆管理

.. tip:: 有关这些功能的更多详细信息，请参考 :ref:`agent` 部分。本章节中，我们重点介绍如何创建 ReAct 智能体并运行。

"""

from agentscope.agent import ReActAgent, AgentBase
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
import asyncio
import os

from agentscope.tool import Toolkit, execute_python_code


# %%
# 创建 ReAct 智能体
# ------------------------------
# 为了提高灵活性，``ReActAgent`` 类在其构造函数中暴露了以下参数：
#
# .. list-table:: ``ReActAgent`` 类的初始化参数
#   :header-rows: 1
#
#   * - 参数
#     - 进一步阅读
#     - 描述
#   * - ``name`` (必需)
#     -
#     - 智能体的名称
#   * - ``sys_prompt`` (必需)
#     -
#     - 智能体的系统提示
#   * - ``model`` (必需)
#     - :ref:`model`
#     - 智能体用于生成响应的模型
#   * - ``formatter`` (必需)
#     - :ref:`prompt`
#     - 提示构建策略，应与使用的模型保持一致
#   * - ``toolkit``
#     - :ref:`tool`
#     - 用于注册/调用工具函数的工具模块
#   * - ``memory``
#     - :ref:`memory`
#     - 用于存储对话历史的短期记忆
#   * - ``long_term_memory``
#     - :ref:`long-term-memory`
#     - 长期记忆
#   * - ``long_term_memory_mode``
#     - :ref:`long-term-memory`
#     - 长期记忆的管理模式：
#
#       - ``agent_control``: 允许智能体通过工具函数自己控制长期记忆
#       - ``static_control``: 在每次回复（reply）的开始/结束时，会自动从长期记忆中检索/记录信息
#       - ``both``: 同时激活上述两种模式
#   * - ``enable_meta_tool``
#     - :ref:`tool`
#     - 是否启用元工具（Meta tool），即允许智能体自主管理工具函数
#   * - ``parallel_tool_calls``
#     - :ref:`agent`
#     - 是否允许并行工具调用
#   * - ``max_iters``
#     -
#     - 智能体生成响应的最大迭代次数
#   * - ``plan_notebook``
#     - :ref:`plan`
#     - 计划模块，允许智能体制定和管理计划与子任务
#   * - ``print_hint_msg``
#     -
#     - 是否在终端打印 ``plan_notebook`` 生成的提示消息
#
# 以 DashScope API 为例，我们创建一个智能体对象如下：


async def creating_react_agent() -> None:
    """创建一个 ReAct 智能体并运行一个简单任务。"""
    # 准备工具
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)

    jarvis = ReActAgent(
        name="Jarvis",
        sys_prompt="你是一个名为 Jarvis 的助手",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=True,
            enable_thinking=False,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    msg = Msg(
        name="user",
        content="你好！Jarvis，用 Python 运行 Hello World。",
        role="user",
    )

    await jarvis(msg)


asyncio.run(creating_react_agent())

# %%
# 从零开始创建
# --------------------------------
# 为了支持开发者从零开始创建智能体，AgentScope 提供了两个基类：
#
# .. list-table::
#   :header-rows: 1
#
#   * - 类
#     - 抽象方法
#     - 描述
#   * - ``AgentBase``
#     - | ``reply``
#       | ``observe``
#       | ``handle_interrupt``
#     - - 所有智能体的基类，支持 ``reply``、``observe`` 和 ``print`` 函数的前置和后置钩子函数。
#       - 在 ``__call__`` 函数内实现了基础的实时介入（Realtime Steering）逻辑。
#   * - ``ReActAgentBase``
#     - | ``reply``
#       | ``observe``
#       | ``handle_interrupt``
#       | ``_reasoning``
#       | ``_acting``
#     - 在 ``AgentBase`` 的基础上添加了两个抽象函数 ``_reasoning`` 和 ``_acting``，以及它们的钩子函数。
#
# 有关智能体类的更多详细信息，请参考 :ref:`agent` 部分。
#
# 以 ``AgentBase`` 类为例，我们可以通过继承它并实现 ``reply`` 方法来创建自定义智能体类。


class MyAgent(AgentBase):
    """自定义智能体类"""

    def __init__(self) -> None:
        """初始化智能体"""
        super().__init__()

        self.name = "Friday"
        self.sys_prompt = "你是一个名为 Friday 的助手。"
        self.model = DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=False,
        )
        self.formatter = DashScopeChatFormatter()
        self.memory = InMemoryMemory()

    async def reply(self, msg: Msg | list[Msg] | None) -> Msg:
        """直接调用大模型，产生回复消息。"""
        await self.memory.add(msg)

        # 准备提示
        prompt = await self.formatter.format(
            [
                Msg("system", self.sys_prompt, "system"),
                *await self.memory.get_memory(),
            ],
        )

        # 调用模型
        response = await self.model(prompt)

        msg = Msg(
            name=self.name,
            content=response.content,
            role="assistant",
        )

        # 在记忆中记录响应
        await self.memory.add(msg)

        # 打印消息
        await self.print(msg)
        return msg

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """观察消息。"""
        # 将消息存储在记忆中
        await self.memory.add(msg)

    async def handle_interrupt(self) -> Msg:
        """处理中断。"""
        # 以固定响应为例
        return Msg(
            name=self.name,
            content="我注意到您打断了我的回复，我能为你做些什么？",
            role="assistant",
        )


async def run_custom_agent() -> None:
    """运行自定义智能体。"""
    agent = MyAgent()
    msg = Msg(
        name="user",
        content="你是谁？",
        role="user",
    )
    await agent(msg)


asyncio.run(run_custom_agent())

# %%
#
# 进一步阅读
# ---------------------
# - :ref:`agent`
# - :ref:`model`
# - :ref:`prompt`
# - :ref:`tool`
#
