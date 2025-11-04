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
# 使用 ReMe 个人长期记忆
# ~~~~~~~~~~~~~~~~~~~~~~~~
#
# .. note:: 在 GitHub 仓库的 ``examples/long_term_memory/reme`` 目录下我们提供了 ReMe 长期记忆的使用示例。
#
# .. code-block:: python
#     :caption: 安装 ReMe 依赖
#
#     from agentscope.memory import ReMePersonalLongTermMemory
#
#     # 创建 ReMe 个人长期记忆实例
#     reme_long_term_memory = ReMePersonalLongTermMemory(
#         agent_name="Friday",
#         user_name="user_123",
#         model=DashScopeChatModel(
#             model_name="qwen3-max",
#             api_key=os.environ.get("DASHSCOPE_API_KEY"),
#             stream=False,
#         ),
#         embedding_model=DashScopeTextEmbedding(
#             model_name="text-embedding-v4",
#             api_key=os.environ.get("DASHSCOPE_API_KEY"),
#             dimensions=1024,
#         ),
#     )
#
#
# ``ReMePersonalLongTermMemory`` 类提供了四个操作长期记忆的方法。
# 它们分别是用于工具调用的 ``record_to_memory`` 和 ``retrieve_from_memory``，
# 以及用于直接调用的 ``record`` 和 ``retrieve``。
#
# 例如下面的例子中，我们使用 ``record_to_memory`` 记录用户偏好。
#
# .. code-block:: python
#     :caption: 创建 ReMe 个人长期记忆实例
#
#     async def test_record_to_memory():
#         """测试 record_to_memory 工具函数接口"""
#         async with reme_long_term_memory:
#             result = await reme_long_term_memory.record_to_memory(
#                 thinking="用户正在分享他们的旅行偏好和习惯",
#                 content=[
#                     "我去杭州旅行时喜欢住民宿",
#                     "我喜欢早上去西湖游玩",
#                     "我喜欢喝龙井茶",
#                 ],
#             )
#             # 提取结果文本
#             result_text = " ".join(
#                 block.get("text", "")
#                 for block in result.content
#                 if block.get("type") == "text"
#             )
#             print(f"记录结果: {result_text}")
#
#
# 然后我们使用 ``retrieve_from_memory`` 检索相关记忆。
#
# .. code-block:: python
#     :caption: 使用 retrieve_from_memory 检索记忆
#
#     async def test_retrieve_from_memory():
#         """测试 retrieve_from_memory 工具函数接口"""
#         async with reme_long_term_memory:
#             # 先记录一些内容
#             await reme_long_term_memory.record_to_memory(
#                 thinking="用户正在分享旅行偏好",
#                 content=["我去杭州旅行时喜欢住民宿"],
#             )
#
#             # 然后检索
#             result = await reme_long_term_memory.retrieve_from_memory(
#                 keywords=["杭州旅行", "茶偏好"],
#             )
#             retrieved_text = " ".join(
#                 block.get("text", "")
#                 for block in result.content
#                 if block.get("type") == "text"
#             )
#             print(f"检索到的记忆: {retrieved_text}")
#
#
# 除了工具函数接口，我们也可以使用 ``record`` 方法直接记录消息对话。
#
# .. code-block:: python
#     :caption: 使用 record 直接记录消息
#
#     async def test_record_direct():
#         """测试 record 直接记录方法"""
#         async with reme_long_term_memory:
#             await reme_long_term_memory.record(
#                 msgs=[
#                     Msg(
#                         role="user",
#                         content="我是一名软件工程师，喜欢远程工作",
#                         name="user",
#                     ),
#                     Msg(
#                         role="assistant",
#                         content="明白了！您是一名重视远程工作灵活性的软件工程师。",
#                         name="assistant",
#                     ),
#                     Msg(
#                         role="user",
#                         content="我通常早上9点开始工作，会先喝一杯咖啡",
#                         name="user",
#                     ),
#                 ],
#             )
#             print("成功记录了对话消息")
#
#
# 类似地，我们使用 ``retrieve`` 方法检索相关记忆。
#
# .. code-block:: python
#     :caption: 使用 retrieve 直接检索消息
#
#     async def test_retrieve_direct():
#         """测试 retrieve 直接检索方法"""
#         async with reme_long_term_memory:
#             # 先记录一些内容
#             await reme_long_term_memory.record(
#                 msgs=[
#                     Msg(
#                         role="user",
#                         content="我是一名软件工程师，喜欢远程工作",
#                         name="user",
#                     ),
#                 ],
#             )
#
#             # 然后检索
#             memories = await reme_long_term_memory.retrieve(
#                 msg=Msg(
#                     role="user",
#                     content="你知道我的工作偏好吗？",
#                     name="user",
#                 ),
#             )
#             print(f"检索到的记忆: {memories if memories else '未找到相关记忆'}")
#
#
# 与 ReAct 智能体集成
# ----------------------------------------
# AgentScope 中的 ``ReActAgent`` 在构造函数中包含 ``long_term_memory`` 和 ``long_term_memory_mode`` 两个参数。
#
# 当 ``long_term_memory_mode`` 设置为 ``"agent_control"`` 或 ``both`` 时，在 ``ReActAgent`` 的构造函数中将
# 注册 ``record_to_memory`` 和 ``retrieve_from_memory`` 工具函数，使智能体能够自主的管理长期记忆。
#
# .. note:: 为了达到最好的效果，``"agent_control"`` 模式可能还需要在系统提示（system prompt）中添加相应的说明。
#
# .. code-block:: python
#     :caption: 创建带有长期记忆的 ReAct 智能体
#
#     # 创建带有长期记忆的 ReAct 智能体（agent_control 模式）
#     async def test_react_agent_with_reme():
#         """测试 ReActAgent 与 ReMe 个人记忆的集成"""
#         async with reme_long_term_memory:
#             agent_with_reme = ReActAgent(
#                 name="Friday",
#                 sys_prompt=(
#                     "你是一个名为 Friday 的助手，具有长期记忆能力。"
#                     "\n\n## 记忆管理指南：\n"
#                     "1. **记录记忆**：当用户分享个人信息、偏好、习惯或关于自己的事实时，"
#                     "始终使用 `record_to_memory` 记录这些信息以供将来参考。\n"
#                     "\n2. **检索记忆**：在回答关于用户偏好、过去信息或个人详细信息的问题之前，"
#                     "你必须首先调用 `retrieve_from_memory` 来检查是否有任何相关的存储信息。"
#                     "不要仅依赖当前对话上下文。\n"
#                     "\n3. **何时检索**：在以下情况下调用 `retrieve_from_memory`：\n"
#                     "   - 用户问类似'我喜欢什么？'、'我的偏好是什么？'、"
#                     "'你对我了解多少？'的问题\n"
#                     "   - 用户询问他们过去的行为、习惯或偏好\n"
#                     "   - 用户提到他们之前提到的信息\n"
#                     "   - 你需要关于用户的上下文来提供个性化的响应\n"
#                     "\n在声称不了解用户的某些信息之前，始终先检查你的记忆。"
#                 ),
#                 model=DashScopeChatModel(
#                     model_name="qwen3-max",
#                     api_key=os.environ.get("DASHSCOPE_API_KEY"),
#                     stream=False,
#                 ),
#                 formatter=DashScopeChatFormatter(),
#                 toolkit=Toolkit(),
#                 memory=InMemoryMemory(),
#                 long_term_memory=reme_long_term_memory,
#                 long_term_memory_mode="agent_control",  # 使用 agent_control 模式
#             )
#
#             # 用户分享偏好
#             msg = Msg(
#                 role="user",
#                 content="我去杭州旅行时，喜欢住民宿",
#                 name="user",
#             )
#             response = await agent_with_reme(msg)
#             print(f"智能体响应: {response.get_text_content()}")
#
#             # 清空短期记忆以测试长期记忆
#             await agent_with_reme.memory.clear()
#
#             # 查询偏好
#             msg2 = Msg(role="user", content="我有什么偏好？", name="user")
#             response2 = await agent_with_reme(msg2)
#             print(f"智能体响应: {response2.get_text_content()}")
#
#
# 然后我们清空智能体的短期记忆，以避免造成干扰，并测试智能体是否会记住之前的对话。
#
# .. code-block:: python
#     :caption: 测试 ReAct 智能体是否记住偏好
#
#     async def retrieve_reme_preferences():
#         """从长期记忆中检索用户偏好"""
#         async with reme_long_term_memory:
#             # 创建智能体（这里可以复用之前创建的智能体，为了示例完整性重新创建）
#             agent_with_reme = ReActAgent(
#                 name="Friday",
#                 sys_prompt="你是一个具有长期记忆功能的助手。",
#                 model=DashScopeChatModel(
#                     api_key=os.environ.get("DASHSCOPE_API_KEY"),
#                     model_name="qwen3-max",
#                     stream=False,
#                 ),
#                 formatter=DashScopeChatFormatter(),
#                 toolkit=Toolkit(),
#                 memory=InMemoryMemory(),
#                 long_term_memory=reme_long_term_memory,
#                 long_term_memory_mode="agent_control",
#             )
#
#             # 我们清空智能体的短期记忆，以避免造成干扰
#             await agent_with_reme.memory.clear()
#
#             # 测试智能体是否会记住之前的对话
#             msg2 = Msg("user", "我有什么偏好？简要的回答我", "user")
#             await agent_with_reme(msg2)
#
#
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
#       - - 如果想支持 "static_control" 模式，必须实现 ``record`` 和 ``retrieve`` 方法。
#         - 想要支持 "agent_control" 模式，必须实现 ``record_to_memory`` 和 ``retrieve_from_memory`` 方法。
#     * - ``Mem0LongTermMemory``
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - 基于 mem0 库的长期记忆实现，支持向量存储和检索。
#     * - ``ReMePersonalLongTermMemory``
#       - | ``record``
#         | ``retrieve``
#         | ``record_to_memory``
#         | ``retrieve_from_memory``
#       - 基于 ReMe 框架的个人记忆实现，提供强大的记忆管理和检索功能。
#
#
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`memory` - 基础记忆系统
# - :ref:`agent` - ReAct 智能体
# - :ref:`tool` - 工具系统
