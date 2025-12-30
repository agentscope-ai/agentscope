# -*- coding: utf-8 -*-
"""
.. _a2a:

A2A 智能体
============================

A2A（Agent-to-Agent）是一种开放标准协议，用于实现不同 AI 智能体之间的互操作通信。

AgentScope 从获取 Agent Card 信息和连接远程智能体两个层面提供对 A2A 协议的支持，涉及到的相关 API 如下：

.. list-table:: A2A 相关类
    :header-rows: 1

    * - 类
      - 描述
    * - ``A2AAgent``
      - 用于与远程 A2A 智能体通信的智能体类
    * - ``A2AChatFormatter``
      - 用于在 AgentScope 消息和 A2A 消息/任务格式之间进行转换的格式化器
    * - ``AgentCardResolverBase``
      - Agent Card 解析器基类
    * - ``FileAgentCardResolver``
      - 从本地 JSON 文件加载 Agent Card 的解析器
    * - ``WellKnownAgentCardResolver``
      - 从 URL 的 well-known 路径获取 Agent Card 的解析器
    * - ``NacosAgentCardResolver``
      - 从 Nacos Agent 注册中心获取 Agent Card 的解析器

本节将演示如何创建 ``A2aAgent`` 并与远程 A2A 智能体进行通信。

.. note:: 注意 A2A 的支持为**实验性功能**，可能会在未来版本中发生变化。同时由于 A2A 协议自身
 的局限性，因此功能上 ``A2AAgent`` 无法完全对齐 ``ReActAgent`` 等本地智能体，包括：

 - 仅支持 chatbot 场景，即仅支持一个用户与一个智能体之间的对话（不影响 handsoff/router 等使用方式）
 - 不支持在对话过程中实时打断
 - 不支持 agentic 结构化输出
 - 目前实现中，``observe`` 方法收到的消息会被存储在本地，并在调用 ``reply`` 方法时一并发送给远程智能体，因此如果最后若干 ``observe`` 调用后未发生 ``reply`` 调用，则这些消息不会被远程智能体看到


"""

# %%
# 获取 Agent Card
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 首先，我们需要获得一个 Agent Card 来连接对应的智能体。Agent Card 中包含了智能体的名称，描述，能力以及连接方式等信息。
#
# 手动创建 Agent Card 对象
# --------------------------------
#
# 在已知 Agent Card 各项信息的情况下，可以直接从 `a2a.types.AgentCard` 手动创建 Agent Card 对象。
#

from a2a.types import AgentCard, AgentCapabilities
from v2.nacos import ClientConfig

from agentscope.a2a import WellKnownAgentCardResolver, NacosAgentCardResolver
from agentscope.agent import A2AAgent, UserAgent
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

# 创建 Agent Card 对象
agent_card = AgentCard(
    name="Friday",  # 智能体名称
    description="一个有趣的聊天伙伴",  # 智能体描述
    url="http://localhost:8000",  # 智能体的 RPC 服务地址
    version="1.0.0",  # 智能体版本
    capabilities=AgentCapabilities(  # 智能体能力配置
        push_notifications=False,
        state_transition_history=True,
        streaming=True,
    ),
    default_input_modes=["text/plain"],  # 支持的输入格式
    default_output_modes=["text/plain"],  # 支持的输出格式
    skills=[],  # 智能体技能列表
)

# %%
#
# 从远程服务获取 Agent Card
# --------------------------------
# 同时，AgentScope 也支持通过多种方式动态获取 Agent Card，包括从本地文件加载、从远程服务 (well-known server) 的标准路径获取以及从 Nacos 注册中心获取等。
# 这里以 ``WellKnownAgentCardResolver`` 为例，从远程服务的标准路径获取 Agent Card：
#


async def agent_card_from_well_known_website() -> AgentCard:
    """从远程服务的 well-known 路径获取 Agent Card 的示例。"""
    # 创建 Agent Card 解析器
    resolver = WellKnownAgentCardResolver(
        base_url="http://localhost:8000",
    )
    # 获取并返回 Agent Card
    return await resolver.get_agent_card()


# %%
# 从本地文件加载 Agent Card
# --------------------------------
#
# ``FileAgentCardResolver`` 类支持从本地 JSON 文件加载 Agent Card，适用于配置文件管理的场景。
# 一个 JSON 格式的 Agent Card 样例如下所示：
#
# .. code-block:: json
#     :caption: 示例 Agent Card JSON 文件内容
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
# 通过 ``FileAgentCardResolver`` 可以方便地加载该文件：
#


async def agent_card_from_file() -> AgentCard:
    """从本地 JSON 文件加载 Agent Card 的示例。"""
    from agentscope.a2a import FileAgentCardResolver

    # 从 JSON 文件加载 Agent Card
    resolver = FileAgentCardResolver(
        file_path="./agent_card.json",  # JSON 文件路径
    )
    # 获取并返回 Agent Card
    return await resolver.get_agent_card()


# %%
# 从 Nacos 注册中心获取 Agent Card
# --------------------------------
#
# Nacos 是一款开源的动态服务发现、配置管理和服务管理平台，在 3.1.0 版本中引入了 Agent 注册中心功能，支持 A2A 智能体的分布式注册、发现和版本管理。
#
# .. important:: 使用 ``NacosAgentCardResolver`` 的前提是用户已经部署了 3.1.0 版本以上的 Nacos 服务端，部署与注册流程请参考`官方文档 <https://nacos.io/docs/latest/quickstart/quick-start>`_。
#


async def agent_card_from_nacos() -> AgentCard:
    """从 Nacos 注册中心获取 Agent Card 的示例。"""

    # 创建 Nacos Agent Card 解析器
    resolver = NacosAgentCardResolver(
        remote_agent_name="my-remote-agent",  # Nacos 中注册的智能体名称
        nacos_client_config=ClientConfig(
            server_addresses="http://localhost:8848",  # Nacos 服务器地址
            # 其他可选配置项
        ),
    )
    # 获取并返回 Agent Card
    return await resolver.get_agent_card()


# %%
# 构建 A2A 智能体
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope 提供的 ``A2AAgent`` 类用于与远程 A2A 智能体进行通信，其使用方式与普通智能体类似。

agent = A2AAgent(agent_card=agent_card)

# %%
# 利用 ``A2AAgent``，开发者可以构建 chatbot 场景的聊天，或是封装成工具函数，从而构建 handsoff/router 等更复杂的应用场景。
# 目前 ``A2AAgent`` 支持的格式协议转换由 ``agentscope.formatter.A2AChatFormatter`` 负责，支持
#
# - 将 AgentScope 的 ``Msg`` 消息转换为 A2A 协议的 ``Message`` 格式
# - 将 A2A 协议的响应转换回 AgentScope 的 ``Msg`` 格式
# - 将 A2A 协议的 ``Task`` 相应转换成 AgentScope 的 ``Msg`` 格式
# - 支持文本、图像、音频、视频等多种内容类型
#


async def a2a_in_chatbot() -> None:
    """使用 A2AAgent 进行聊天的示例。"""

    user = UserAgent("user")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


# %%
# 或是如下封装成工具函数用于调用：


async def create_worker(query: str) -> ToolResponse:
    """通过子智能体完成给定的任务

    Args:
        query (`str`):
            需要子智能体完成的任务描述
    """
    res = await agent(
        Msg("user", query, "user"),
    )
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=res.get_text_content(),
            ),
        ],
    )
