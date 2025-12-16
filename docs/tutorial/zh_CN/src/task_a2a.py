# -*- coding: utf-8 -*-
"""
.. _a2a:

A2A 智能体
============================

A2A（Agent-to-Agent）是一种开放标准协议，用于实现不同 AI 智能体之间的互操作通信。

AgentScope 通过 ``A2aAgent`` 类提供了对 A2A 协议的内置支持，让开发者可以与任何符合 A2A 标准的远程智能体进行通信。

相关 API 如下：

.. list-table:: A2A 相关类
    :header-rows: 1

    * - 类
      - 描述
    * - ``A2aAgent``
      - 用于与远程 A2A 智能体通信的智能体
    * - ``A2aAgentConfig``
      - A2A 客户端的配置类
    * - ``FixedAgentCardResolver``
      - 使用固定 Agent Card 的解析器
    * - ``FileAgentCardResolver``
      - 从本地 JSON 文件加载 Agent Card 的解析器
    * - ``WellKnownAgentCardResolver``
      - 从 URL 的 well-known 路径获取 Agent Card 的解析器
    * - ``NacosAgentCardResolver``
      - 从 Nacos Agent 注册中心获取 Agent Card 的解析器

本节将演示如何创建 ``A2aAgent`` 并与远程 A2A 智能体进行通信。
"""

# %%
# 创建 A2aAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 首先，我们需要创建一个 Agent Card 解析器来获取远程智能体的信息。
# 这里以 ``WellKnownAgentCardResolver`` 为例，从远程服务的标准路径获取 Agent Card：
#
# .. code-block:: python
#
#     from agentscope.agent import A2aAgent
#     from agentscope.agent._a2a_agent import A2aAgentConfig
#     from agentscope.agent._a2a_card_resolver import WellKnownAgentCardResolver
#
#     # 创建 Agent Card 解析器
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#     )
#
#     # 创建 A2A 智能体
#     agent = A2aAgent(
#         name="RemoteAgent",
#         agent_card=resolver,
#         agent_config=A2aAgentConfig(
#             streaming=True,  # 启用流式响应
#         ),
#     )
#

# %%
# Agent Card 解析器
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# Agent Card 是描述远程智能体能力和访问方式的元数据。
# AgentScope 提供了多种解析器来获取 Agent Card。
#
# FixedAgentCardResolver
# --------------------------------
#
# 使用固定的 Agent Card 对象，适用于已知远程智能体信息的场景。
#
# .. list-table:: FixedAgentCardResolver 参数
#     :header-rows: 1
#
#     * - 参数
#       - 类型
#       - 描述
#     * - ``agent_card``
#       - AgentCard
#       - A2A Agent Card 对象，包含远程智能体的完整信息
#
# .. code-block:: python
#
#     from a2a.types import AgentCard, AgentCapabilities
#     from agentscope.agent._a2a_card_resolver import FixedAgentCardResolver
#
#     # 创建 Agent Card 对象
#     agent_card = AgentCard(
#         name="RemoteAgent",              # 智能体名称
#         url="http://localhost:8000",     # 智能体的 RPC 服务地址
#         version="1.0.0",                 # 智能体版本
#         capabilities=AgentCapabilities(),  # 智能体能力配置
#         default_input_modes=["text/plain"],   # 支持的输入格式
#         default_output_modes=["text/plain"],  # 支持的输出格式
#         skills=[],                       # 智能体技能列表
#     )
#
#     resolver = FixedAgentCardResolver(agent_card)
#
# FileAgentCardResolver
# --------------------------------
#
# 从本地 JSON 文件加载 Agent Card，适用于配置文件管理的场景。
#
# .. list-table:: FileAgentCardResolver 参数
#     :header-rows: 1
#
#     * - 参数
#       - 类型
#       - 描述
#     * - ``file_path``
#       - str
#       - Agent Card JSON 文件的路径
#
# JSON 文件格式示例：
#
# .. code-block:: json
#
#     {
#         "name": "RemoteAgent",
#         "url": "http://localhost:8000",
#         "description": "远程 A2A 智能体",
#         "version": "1.0.0",
#         "capabilities": {},
#         "default_input_modes": ["text/plain"],
#         "default_output_modes": ["text/plain"],
#         "skills": []
#     }
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import FileAgentCardResolver
#
#     # 从 JSON 文件加载 Agent Card
#     resolver = FileAgentCardResolver(
#         file_path="./agent_card.json",  # JSON 文件路径
#     )
#
# WellKnownAgentCardResolver
# --------------------------------
#
# 从远程服务的 well-known 路径获取 Agent Card，这是 A2A 协议的标准服务发现方式。
# 默认会从 ``{base_url}/.well-known/agent.json`` 获取。
#
# .. list-table:: WellKnownAgentCardResolver 参数
#     :header-rows: 1
#
#     * - 参数
#       - 类型
#       - 描述
#     * - ``base_url``
#       - str
#       - 远程智能体的基础 URL（如 http://localhost:8000）
#     * - ``agent_card_path``
#       - str | None
#       - 可选，自定义 Agent Card 路径，默认使用 A2A 标准路径
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import WellKnownAgentCardResolver
#
#     # 使用默认的 well-known 路径
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#     )
#
#     # 或者指定自定义路径
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#         agent_card_path="/custom/agent-card.json",
#     )
#
# NacosAgentCardResolver
# --------------------------------
#
# 从 Nacos Agent 注册中心获取 Agent Card。Nacos 在 3.1.0 版本中实现了 Agent 注册中心能力，
# 支持 A2A 智能体的分布式注册、发现和版本管理。
#
# .. important:: 使用 ``NacosAgentCardResolver`` 的前提是用户已经部署了 3.1.0 版本以上的 Nacos 服务端。
#
# Nacos 快速部署和 Agent 注册中心详细介绍请参考：https://nacos.io/docs/latest/quickstart/quick-start
#
# .. list-table:: NacosAgentCardResolver 参数
#     :header-rows: 1
#
#     * - 参数
#       - 类型
#       - 描述
#     * - ``remote_agent_name``
#       - str
#       - 远程智能体在 Nacos 中注册的名称
#     * - ``nacos_client_config``
#       - ClientConfig | None
#       - Nacos 客户端配置，为 None 时从环境变量读取
#     * - ``version``
#       - str | None
#       - 可选，指定要获取的智能体版本。为 None 时获取最新版本
#
# **方式 1：使用环境变量配置**
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import NacosAgentCardResolver
#
#     # 需要设置 NACOS_SERVER_ADDR 环境变量
#     resolver = NacosAgentCardResolver(
#         remote_agent_name="my-remote-agent",  # Nacos 中注册的智能体名称
#     )
#
# 支持的环境变量：
#
# - ``NACOS_SERVER_ADDR``: Nacos 服务器地址（必需，如 localhost:8848）
# - ``NACOS_USERNAME``: 用户名（可选）
# - ``NACOS_PASSWORD``: 密码（可选）
# - ``NACOS_NAMESPACE_ID``: 命名空间 ID（可选）
# - ``NACOS_ACCESS_KEY``: 阿里云 AccessKey（可选）
# - ``NACOS_SECRET_KEY``: 阿里云 SecretKey（可选）
#
# **方式 2：使用 ClientConfig 配置**
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import NacosAgentCardResolver
#     from v2.nacos.common.client_config_builder import ClientConfigBuilder
#
#     # 构建 Nacos 客户端配置
#     config = (
#         ClientConfigBuilder()
#         .server_address("localhost:8848")
#         .username("nacos")
#         .password("nacos")
#         .build()
#     )
#
#     resolver = NacosAgentCardResolver(
#         remote_agent_name="my-remote-agent",
#         nacos_client_config=config,
#         version="1.0.0",  # 可选，指定版本；不指定则获取最新版本
#     )
#

# %%
# 与远程智能体通信
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgent`` 提供了与其他 AgentScope 智能体一致的接口：
#
# .. code-block:: python
#
#     from agentscope.message import Msg
#
#     async def main():
#         # 发送消息并获取响应
#         response = await agent(
#             Msg("user", "你好，请介绍一下你自己", "user"),
#         )
#         print(response.content)
#
#     asyncio.run(main())
#
# ``A2aAgent`` 会自动处理以下转换：
#
# - AgentScope 的 ``Msg`` 消息会被转换为 A2A 协议的 ``Message`` 格式
# - A2A 协议的响应会被转换回 AgentScope 的 ``Msg`` 格式
# - 支持文本、图像、音频、视频等多种内容类型
#

# %%
# A2aAgentConfig 配置
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgentConfig`` 用于配置 A2A 客户端的行为：
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_agent import A2aAgentConfig
#
#     config = A2aAgentConfig(
#         streaming=True,           # 是否支持流式响应
#         polling=False,            # 是否使用轮询模式
#         use_client_preference=False,  # 是否使用客户端传输偏好
#     )
#

# %%
# observe 方法
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgent`` 支持 ``observe`` 方法，用于接收消息但不立即生成回复：
#
# .. code-block:: python
#
#     async def example_observe():
#         # 观察消息（不生成回复）
#         await agent.observe(Msg("user", "这是一条上下文信息", "user"))
#
#         # 后续调用时，观察到的消息会被合并处理
#         response = await agent(Msg("user", "基于上下文回答问题", "user"))
#

# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`agent`
# - :ref:`mcp`
#

