# -*- coding: utf-8 -*-
"""
.. _mcp:

MCP
=========================

本章将介绍 AgentScope 对 MCP（Model Context Protocol）的以下支持：

- 支持 **HTTP** （StreamableHTTP 和 SSE）和 **StdIO** 类型的 MCP 服务器
- 提供 **有状态** 和 **无状态** 两种 MCP 客户端
- 提供 **MCP 级别** 和 **函数级别** 的 MCP 工具管理

这里的有状态/无状态是指客户端是否会维持与 MCP 服务器的会话（session）。
无状态客户端只会在调用工具发生时建立会话，并在工具调用结束后立即销毁会话，是一种轻量化的使用方式。

下表总结了支持的 MCP 客户端类型和协议：

.. list-table:: 支持的 MCP 客户端类型和协议
    :header-rows: 1

    * - 客户端类型
      - HTTP（StreamableHTTP 和 SSE）
      - StdIO
    * - 有状态客户端
      - ``HttpStatefulClient``
      - ``StdIOStatefulClient``
    * - 无状态客户端
      - ``HttpStatelessClient``
      -

"""
import asyncio
import json
import os

from agentscope.mcp import HttpStatefulClient, HttpStatelessClient
from agentscope.tool import Toolkit

# %%
# MCP 客户端
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 在 AgentScope 中，MCP 客户端负责
#
# - 连接到 MCP 服务器，
# - 从服务器获取工具函数，以及
# - 调用 MCP 服务器中的工具函数。
#
# AgentScope 中有两种类型的 MCP 客户端：**有状态** 和 **无状态**。
# 它们仅在 **如何管理与 MCP 服务器的会话** 方面有所不同。
#
# - 有状态客户端：有状态 MCP 客户端在其生命周期内 **维持与 MCP 服务器的持久会话**。开发者应显式调用 ``connect()`` 和 ``close()`` 方法来管理会话的生命周期。
# - 无状态客户端：无状态 MCP 客户端在调用工具函数时创建新会话，在工具函数调用完成后立即销毁会话，更加轻量化。
#
# .. note:: - StdIO MCP 服务器只有有状态客户端，当调用 ``connect()`` 时，它将在本地启动 MCP 服务器然后连接到它。
#  - 对于有状态客户端，开发者必须确保在调用工具函数时客户端已连接。
#  - 当有多个 `HttpStatefulClient` 或 `StdIOStatefulClient` 建立连接时，应按照后进先出 (LIFO) 的顺序关闭它们以避免引发错误。
#
# 以高德地图 MCP 服务器为例，有状态和无状态客户端的创建非常相似：
#

stateful_client = HttpStatefulClient(
    # 用于标识 MCP 的名称
    name="mcp_services_stateful",
    transport="streamable_http",
    url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
)

stateless_client = HttpStatelessClient(
    # 用于标识 MCP 的名称
    name="mcp_services_stateless",
    transport="streamable_http",
    url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
)

# %%
# 有状态和无状态客户端都提供以下方法：
#
# .. list-table:: MCP 客户端方法
#    :header-rows: 1
#
#    * - 方法
#      - 描述
#    * - ``list_tools``
#      - 列出 MCP 服务器中所有可用的工具。
#    * - ``get_callable_function``
#      - 通过名称从 MCP 服务器获取可调用的函数对象。
#
# MCP 作为工具
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 提供了对 MCP 工具的细粒度管理，包括 MCP 级别和函数级别的管理。
#
# MCP 级别管理
# --------------------------------
# 您可以将 MCP 服务器的所有工具一次性注册到 ``Toolkit`` 中，如下所示。
#
# .. tip:: 可选地，开发者可以通过指定组名来管理工具。有关分组工具管理，请参考 :ref:`tool` 部分。
#

toolkit = Toolkit()


async def example_register_stateless_mcp() -> None:
    """注册无状态客户端 MCP 工具的示例。"""
    # 从 MCP 服务器注册所有工具
    await toolkit.register_mcp_client(
        stateless_client,
        # group_name="map_services",  # 可选的组名
    )

    print("注册的 MCP 工具总数：", len(toolkit.get_json_schemas()))

    maps_geo = next(
        tool
        for tool in toolkit.get_json_schemas()
        if tool["function"]["name"] == "maps_geo"
    )
    print("\n示例 ``maps_geo`` 函数：")
    print(
        json.dumps(
            maps_geo,
            indent=4,
            ensure_ascii=False,
        ),
    )


asyncio.run(example_register_stateless_mcp())

# %%
# 要移除已注册的工具，可以使用 ``remove_tool_function`` 函数，或使用 ``remove_mcp_clients`` 移除特定 MCP 的所有工具。
#


async def example_remove_mcp_tools() -> None:
    """移除 MCP 工具的示例。"""
    print("移除前的工具总数：", len(toolkit.get_json_schemas()))

    # 通过名称移除特定的工具函数
    toolkit.remove_tool_function("maps_geo")
    print("工具数量：", len(toolkit.get_json_schemas()))

    # 通过名称移除 MCP 客户端的所有工具
    await toolkit.remove_mcp_clients(client_names=["mcp_services_stateless"])
    print("工具数量：", len(toolkit.get_json_schemas()))


asyncio.run(example_remove_mcp_tools())

# %%
# 函数级别管理
# --------------------------------
# 注意到开发者有对 MCP 工具进行更细粒度控制的需求，例如对工具结果进行后处理，或使用它们创建更复杂的工具函数。
#
# 因此，AgentScope 支持通过工具名从 MCP 客户端获取可调用的函数对象，这样开发者可以
#
# - 直接调用它，
# - 将其包装到自己的函数中，或以任何其它方式进行使用。
#
# 此外，开发者可以指定是否将工具函数执行结果包装成 ``ToolResponse`` 对象，以便与 ``Toolkit`` 无缝使用。
# 如果设置 ``wrap_tool_result=False``，将返回原始结果类型 ``mcp.types.CallToolResult``。
#
# 以 ``maps_geo`` 函数为例，可以将其获取为可调用的函数对象，如下所示：
#


async def example_function_level_usage() -> None:
    """使用函数级别 MCP 工具的示例。"""
    func_obj = await stateless_client.get_callable_function(
        func_name="maps_geo",
        # 是否将工具结果包装到 AgentScope 的 ToolResponse 中
        wrap_tool_result=True,
    )

    # 您可以获取其名称、描述和 JSON schema
    print("函数名称：", func_obj.name)
    print("函数描述：", func_obj.description)
    print(
        "函数 JSON schema：",
        json.dumps(func_obj.json_schema, indent=4, ensure_ascii=False),
    )

    # 直接调用函数对象
    res = await func_obj(
        address="天安门广场",
        city="北京",
    )
    print("\n函数调用结果：")
    print(res)


asyncio.run(example_function_level_usage())

# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 有关更多详细信息，请参见：
#
# - :ref:`tool`
# - :ref:`agent`
#
