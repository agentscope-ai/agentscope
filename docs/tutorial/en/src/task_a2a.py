# -*- coding: utf-8 -*-
"""
.. _a2a:

A2A Agent
============================

A2A (Agent-to-Agent) is an open standard protocol for enabling interoperable communication between different AI agents.

AgentScope provides built-in support for the A2A protocol through the ``A2aAgent`` class, allowing developers to communicate with any remote agent that conforms to the A2A standard.

The relevant APIs are as follows:

.. list-table:: A2A Related Classes
    :header-rows: 1

    * - Class
      - Description
    * - ``A2aAgent``
      - Agent for communicating with remote A2A agents
    * - ``A2aAgentConfig``
      - Configuration class for A2A client
    * - ``FixedAgentCardResolver``
      - Resolver that uses a fixed Agent Card
    * - ``FileAgentCardResolver``
      - Resolver that loads Agent Card from a local JSON file
    * - ``WellKnownAgentCardResolver``
      - Resolver that fetches Agent Card from a URL's well-known path
    * - ``NacosAgentCardResolver``
      - Resolver that fetches Agent Card from Nacos Agent Registry

This section demonstrates how to create an ``A2aAgent`` and communicate with remote A2A agents.
"""

# %%
# Creating A2aAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# First, we need to create an Agent Card resolver to obtain information about the remote agent.
# Here we use ``WellKnownAgentCardResolver`` as an example to fetch the Agent Card from a remote service's standard path:
#
# .. code-block:: python
#
#     from agentscope.agent import A2aAgent
#     from agentscope.agent._a2a_agent import A2aAgentConfig
#     from agentscope.agent._a2a_card_resolver import WellKnownAgentCardResolver
#
#     # Create Agent Card resolver
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#     )
#
#     # Create A2A agent
#     agent = A2aAgent(
#         name="RemoteAgent",
#         agent_card=resolver,
#         agent_config=A2aAgentConfig(
#             streaming=True,  # Enable streaming responses
#         ),
#     )
#

# %%
# Agent Card Resolvers
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# An Agent Card is metadata that describes the capabilities and access methods of a remote agent.
# AgentScope provides several resolvers to obtain Agent Cards.
#
# FixedAgentCardResolver
# --------------------------------
#
# Uses a fixed Agent Card object, suitable for scenarios where remote agent information is already known.
#
# .. list-table:: FixedAgentCardResolver Parameters
#     :header-rows: 1
#
#     * - Parameter
#       - Type
#       - Description
#     * - ``agent_card``
#       - AgentCard
#       - A2A Agent Card object containing complete information about the remote agent
#
# .. code-block:: python
#
#     from a2a.types import AgentCard, AgentCapabilities
#     from agentscope.agent._a2a_card_resolver import FixedAgentCardResolver
#
#     # Create Agent Card object
#     agent_card = AgentCard(
#         name="RemoteAgent",              # Agent name
#         url="http://localhost:8000",     # Agent's RPC service URL
#         version="1.0.0",                 # Agent version
#         capabilities=AgentCapabilities(),  # Agent capabilities config
#         default_input_modes=["text/plain"],   # Supported input formats
#         default_output_modes=["text/plain"],  # Supported output formats
#         skills=[],                       # Agent skills list
#     )
#
#     resolver = FixedAgentCardResolver(agent_card)
#
# FileAgentCardResolver
# --------------------------------
#
# Loads Agent Card from a local JSON file, suitable for configuration file management scenarios.
#
# .. list-table:: FileAgentCardResolver Parameters
#     :header-rows: 1
#
#     * - Parameter
#       - Type
#       - Description
#     * - ``file_path``
#       - str
#       - Path to the Agent Card JSON file
#
# Example JSON file format:
#
# .. code-block:: json
#
#     {
#         "name": "RemoteAgent",
#         "url": "http://localhost:8000",
#         "description": "A remote A2A agent",
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
#     # Load Agent Card from JSON file
#     resolver = FileAgentCardResolver(
#         file_path="./agent_card.json",  # JSON file path
#     )
#
# WellKnownAgentCardResolver
# --------------------------------
#
# Fetches Agent Card from a remote service's well-known path, which is the standard A2A protocol service discovery method.
# By default, it fetches from ``{base_url}/.well-known/agent.json``.
#
# .. list-table:: WellKnownAgentCardResolver Parameters
#     :header-rows: 1
#
#     * - Parameter
#       - Type
#       - Description
#     * - ``base_url``
#       - str
#       - Base URL of the remote agent (e.g., http://localhost:8000)
#     * - ``agent_card_path``
#       - str | None
#       - Optional, custom Agent Card path, defaults to A2A standard path
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import WellKnownAgentCardResolver
#
#     # Use default well-known path
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#     )
#
#     # Or specify custom path
#     resolver = WellKnownAgentCardResolver(
#         base_url="http://localhost:8000",
#         agent_card_path="/custom/agent-card.json",
#     )
#
# NacosAgentCardResolver
# --------------------------------
#
# Fetches Agent Card from Nacos Agent Registry. Nacos introduced the Agent Registry capability in version 3.1.0,
# enabling distributed registration, discovery, and version management of A2A Agents.
#
# .. important:: Using ``NacosAgentCardResolver`` requires that you have deployed Nacos server version 3.1.0 or above.
#
# For Nacos quick deployment and Agent Registry details, please refer to: https://nacos.io/docs/latest/quickstart/quick-start
#
# .. list-table:: NacosAgentCardResolver Parameters
#     :header-rows: 1
#
#     * - Parameter
#       - Type
#       - Description
#     * - ``remote_agent_name``
#       - str
#       - Name of the remote agent registered in Nacos
#     * - ``nacos_client_config``
#       - ClientConfig | None
#       - Nacos client configuration, reads from environment variables when None
#     * - ``version``
#       - str | None
#       - Optional, specify the agent version to fetch. Fetches latest version when None
#
# **Method 1: Using Environment Variables**
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import NacosAgentCardResolver
#
#     # Requires NACOS_SERVER_ADDR environment variable to be set
#     resolver = NacosAgentCardResolver(
#         remote_agent_name="my-remote-agent",  # Agent name registered in Nacos
#     )
#
# Supported environment variables:
#
# - ``NACOS_SERVER_ADDR``: Nacos server address (required, e.g., localhost:8848)
# - ``NACOS_USERNAME``: Username (optional)
# - ``NACOS_PASSWORD``: Password (optional)
# - ``NACOS_NAMESPACE_ID``: Namespace ID (optional)
# - ``NACOS_ACCESS_KEY``: Alibaba Cloud AccessKey (optional)
# - ``NACOS_SECRET_KEY``: Alibaba Cloud SecretKey (optional)
#
# **Method 2: Using ClientConfig**
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_card_resolver import NacosAgentCardResolver
#     from v2.nacos.common.client_config_builder import ClientConfigBuilder
#
#     # Build Nacos client configuration
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
#         version="1.0.0",  # Optional; fetches latest version if not specified
#     )
#

# %%
# Communicating with Remote Agents
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgent`` provides an interface consistent with other AgentScope agents:
#
# .. code-block:: python
#
#     from agentscope.message import Msg
#
#     async def main():
#         # Send message and get response
#         response = await agent(
#             Msg("user", "Hello, please introduce yourself", "user"),
#         )
#         print(response.content)
#
#     asyncio.run(main())
#
# ``A2aAgent`` automatically handles the following conversions:
#
# - AgentScope's ``Msg`` messages are converted to A2A protocol's ``Message`` format
# - A2A protocol responses are converted back to AgentScope's ``Msg`` format
# - Supports multiple content types including text, images, audio, and video
#

# %%
# A2aAgentConfig Configuration
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgentConfig`` is used to configure the behavior of the A2A client:
#
# .. code-block:: python
#
#     from agentscope.agent._a2a_agent import A2aAgentConfig
#
#     config = A2aAgentConfig(
#         streaming=True,           # Whether to support streaming responses
#         polling=False,            # Whether to use polling mode
#         use_client_preference=False,  # Whether to use client transport preferences
#     )
#

# %%
# The observe Method
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# ``A2aAgent`` supports the ``observe`` method for receiving messages without immediately generating a reply:
#
# .. code-block:: python
#
#     async def example_observe():
#         # Observe message (without generating reply)
#         await agent.observe(Msg("user", "This is context information", "user"))
#
#         # When called later, observed messages are merged
#         response = await agent(Msg("user", "Answer based on the context", "user"))
#

# %%
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`agent`
# - :ref:`mcp`
#

