# -*- coding: utf-8 -*-
"""
.. _mcp:

MCP
=========================

The tutorial covers the following features of AgentScope in support of the MCP (Model Context Protocol):

- Support both **HTTP** (streamable HTTP and SSE) and **StdIO** MCP servers
- Provide both **stateful** and **stateless** MCP clients
- Provide both **server-level** and **function-level** MCP tool management

Here the stateful/stateless distinction refers to whether the client maintains a persistent session with the MCP server or not.
The table below summarizes the supported MCP client types and protocols:

.. list-table:: Supported MCP client types and protocols
    :header-rows: 1

    * - Client Type
      - HTTP (Streamable HTTP and SSE)
      - StdIO
    * - Stateful Client
      - ``HttpStatefulClient``
      - ``HttpStatelessClient``
    * - Stateless Client
      - ``StdIOStatefulClient``
      -

"""
import asyncio
import json
import os

from agentscope.mcp import HttpStatefulClient, HttpStatelessClient
from agentscope.tool import Toolkit

# %%
# MCP Client
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# In AgentScope, MCP clients are responsible for
#
# - connecting to the MCP server,
# - obtaining tool functions from the server, and
# - calling tool functions in the MCP server.
#
# There are two types of MCP clients in AgentScope: **Stateful** and **Stateless**.
# They only differ in how to manage the session with the MCP server.
#
# - Stateful Client: The stateful MCP client **maintains a persistent session** with the MCP server within its lifetime. The developers should explicitly call ``connect()`` and ``close()`` methods to manage the connection lifecycle.
# - Stateless Client: The stateless MCP client creates a new session when calling the tool function, and destroys the session right after the tool function call, which is much more lightweight.
#
# .. note:: - The StdIO MCP server only has stateful client, when ``connect()`` is called, it will start the MCP server locally and then connect to it.
#  - For stateful clients, developers must ensure the client is connected when calling the tool functions.
#  - When multiple `HttpStatefulClients` or `StdIOStatefulClients` are connected, they should be closed in Last In First Out (LIFO) order to prevent errors.
#
# Taking Gaode map MCP server as an example, the creation of stateful and stateless clients are very similar:
#

stateful_client = HttpStatefulClient(
    # The name to identify the MCP
    name="mcp_services_stateful",
    transport="streamable_http",
    url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
)

stateless_client = HttpStatelessClient(
    # The name to identify the MCP
    name="mcp_services_stateless",
    transport="streamable_http",
    url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
)

# %%
# Both stateful and stateless clients provide the following methods:
#
# .. list-table:: MCP Client Methods
#    :header-rows: 1
#
#    * - Method
#      - Description
#    * - ``list_tools``
#      - List all tools available in the MCP server.
#    * - ``get_callable_function``
#      - Get a callable function object from the MCP server by its name.
#
# MCP as Tool
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope provides fine-grained management of MCP tools, including both server-level and function-level management.
#
# Server-Level Management
# --------------------------------
# You can register all tools from an MCP server into ``Toolkit`` as follows.
#
# .. tip:: Optionally, you can specify a group name to organize the tools. Refer to :ref:`tool` section for group-wise tools management.
#

toolkit = Toolkit()


async def example_register_stateless_mcp() -> None:
    """Example of registering MCP tools from a stateless client."""
    # Register all tools from the MCP server
    await toolkit.register_mcp_client(
        stateless_client,
        # group_name="map_services",  # Optional group name
    )

    print(
        "Total number of MCP tools registered:",
        len(toolkit.get_json_schemas()),
    )

    maps_geo = next(
        tool
        for tool in toolkit.get_json_schemas()
        if tool["function"]["name"] == "maps_geo"
    )
    print("\nThe example ``maps_geo`` function:")
    print(
        json.dumps(
            maps_geo,
            indent=4,
            ensure_ascii=False,
        ),
    )


asyncio.run(example_register_stateless_mcp())

# %%
# To remove the registered tools, you can use the ``remove_tool_function`` to remove a specific tool function, or ``remove_mcp_clients`` to remove all tools from a specific MCP.
#


async def example_remove_mcp_tools() -> None:
    """Example of removing MCP tools."""
    print(
        "Total number of tools before removal: ",
        len(toolkit.get_json_schemas()),
    )

    # Remove a specific tool function by its name
    toolkit.remove_tool_function("maps_geo")
    print("Number of tools: ", len(toolkit.get_json_schemas()))

    # Remove all tools from the MCP client by its name
    await toolkit.remove_mcp_clients(client_names=["mcp_services_stateless"])
    print("Number of tools: ", len(toolkit.get_json_schemas()))


asyncio.run(example_remove_mcp_tools())

# %%
# Function-Level Management
# --------------------------------
# We notice the demand for more fine-grained control over MCP tools, such as post-processing the tool results, or use them to create a more complex tool function.
#
# Therefore, AgentScope supports to obtain the callable function object from MCP by its name, so that you can
#
# - call it directly,
# - wrap it into your own function, or anyway you like.
#
# Additionally, you can specify whether to wrap the tool result into ``ToolResponse`` object in AgentScope, so that you can use it seamlessly with the ``Toolkit``.
# If you set ``wrap_tool_result=False``, the raw result type ``mcp.types.CallToolResult`` will be returned.
#
# Taking the ``maps_geo`` function as an example, you can obtain it as a callable function object as follows:
#


async def example_function_level_usage() -> None:
    """Example of using function-level MCP tool."""
    func_obj = await stateless_client.get_callable_function(
        func_name="maps_geo",
        # Whether to wrap the tool result into ToolResponse in AgentScope
        wrap_tool_result=True,
    )

    # You can obtain its name, description and json schema
    print("Function name:", func_obj.name)
    print("Function description:", func_obj.description)
    print(
        "Function JSON schema:",
        json.dumps(func_obj.json_schema, indent=4, ensure_ascii=False),
    )

    # Call the function object directly
    res = await func_obj(
        address="Tiananmen Square",
        city="Beijing",
    )
    print("\nFunction call result:")
    print(res)


asyncio.run(example_function_level_usage())

# %%
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# For more details, see:
#
# - :ref:`tool`
# - :ref:`agent`
#
