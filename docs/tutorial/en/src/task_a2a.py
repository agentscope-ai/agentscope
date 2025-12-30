# -*- coding: utf-8 -*-
"""
.. _a2a:

A2A Agent
============================

A2A (Agent-to-Agent) is an open standard protocol for enabling interoperable communication between different AI agents.

AgentScope provides support for the A2A protocol at two levels: obtaining Agent Card information and connecting to remote agents. The related APIs are as follows:

.. list-table:: A2A Related Classes
    :header-rows: 1

    * - Class
      - Description
    * - ``A2AAgent``
      - Agent class for communicating with remote A2A agents
    * - ``A2AChatFormatter``
      - Formatter for converting between AgentScope messages and A2A message/task formats
    * - ``AgentCardResolverBase``
      - Base class for Agent Card resolvers
    * - ``FileAgentCardResolver``
      - Resolver for loading Agent Cards from local JSON files
    * - ``WellKnownAgentCardResolver``
      - Resolver for fetching Agent Cards from the well-known path of a URL
    * - ``NacosAgentCardResolver``
      - Resolver for fetching Agent Cards from the Nacos Agent Registry

This section demonstrates how to create an ``A2AAgent`` and communicate with remote A2A agents.

.. note:: Note that A2A support is an **experimental feature** and may change in future versions. Due to limitations of the A2A protocol itself, ``A2AAgent`` cannot fully align with local agents like ``ReActAgent``, including:

 - Only supports chatbot scenarios, i.e., only supports conversations between one user and one agent (does not affect handoff/router usage patterns)
 - Does not support real-time interruption during conversations
 - Does not support agentic structured output
 - In the current implementation, messages received by the ``observe`` method are stored locally and sent to the remote agent together when the ``reply`` method is called. Therefore, if several ``observe`` calls are made without a subsequent ``reply`` call, those messages will not be seen by the remote agent


"""

from a2a.types import AgentCard, AgentCapabilities
from v2.nacos import ClientConfig

from agentscope.a2a import WellKnownAgentCardResolver, NacosAgentCardResolver
from agentscope.agent import A2AAgent, UserAgent
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse

# %%
# Obtaining Agent Cards
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# First, we need to obtain an Agent Card to connect to the corresponding agent. An Agent Card contains information such as the agent's name, description, capabilities, and connection details.
#
# Manually Creating Agent Card
# --------------------------------
#
# If you know all the information of an Agent Card, you can directly create an Agent Card object from `a2a.types.AgentCard`.
#

# Create an Agent Card object
agent_card = AgentCard(
    name="Friday",  # Agent name
    description="A fun chatting companion",  # Agent description
    url="http://localhost:8000",  # Agent's RPC service URL
    version="1.0.0",  # Agent version
    capabilities=AgentCapabilities(  # Agent capability configuration
        push_notifications=False,
        state_transition_history=True,
        streaming=True,
    ),
    default_input_modes=["text/plain"],  # Supported input formats
    default_output_modes=["text/plain"],  # Supported output formats
    skills=[],  # Agent skill list
)

# %%
#
# Fetching from Remote Services
# --------------------------------
# AgentScope also supports fetching from the standard path of remote services (well-known server).
# Here's an example using ``WellKnownAgentCardResolver`` to fetch an Agent Card from the standard path of a remote service:
#


async def agent_card_from_well_known_website() -> AgentCard:
    """Example of fetching an Agent Card from the well-known path of a remote service."""
    # Create an Agent Card resolver
    resolver = WellKnownAgentCardResolver(
        base_url="http://localhost:8000",
    )
    # Fetch and return the Agent Card
    return await resolver.get_agent_card()


# %%
# Loading Agent Cards from Local Files
# --------------------------------
#
# The ``FileAgentCardResolver`` class supports loading Agent Cards from local JSON files, suitable for configuration file management scenarios.
# An example of an Agent Card in JSON format is shown below:
#
# .. code-block:: json
#     :caption: Example Agent Card JSON file content
#
#     {
#         "name": "RemoteAgent",
#         "url": "http://localhost:8000",
#         "description": "Remote A2A Agent",
#         "version": "1.0.0",
#         "capabilities": {},
#         "default_input_modes": ["text/plain"],
#         "default_output_modes": ["text/plain"],
#         "skills": []
#     }
#
# You can easily load this file using ``FileAgentCardResolver``:
#


async def agent_card_from_file() -> AgentCard:
    """Example of loading an Agent Card from a local JSON file."""
    from agentscope.a2a import FileAgentCardResolver

    # Load Agent Card from JSON file
    resolver = FileAgentCardResolver(
        file_path="./agent_card.json",  # JSON file path
    )
    # Fetch and return the Agent Card
    return await resolver.get_agent_card()


# %%
# Fetching Agent Cards from Nacos Registry
# --------------------------------
#
# Nacos is an open-source dynamic service discovery, configuration management, and service management platform. In version 3.1.0, it introduced the Agent Registry feature, supporting distributed registration, discovery, and version management of A2A agents.
#
# .. important:: The prerequisite for using ``NacosAgentCardResolver`` is that the user has deployed a Nacos server version 3.1.0 or higher. For deployment and registration procedures, please refer to the `official documentation <https://nacos.io/docs/latest/quickstart/quick-start>`_.
#


async def agent_card_from_nacos() -> AgentCard:
    """Example of fetching an Agent Card from the Nacos registry."""

    # Create a Nacos Agent Card resolver
    resolver = NacosAgentCardResolver(
        remote_agent_name="my-remote-agent",  # Agent name registered in Nacos
        nacos_client_config=ClientConfig(
            server_addresses="http://localhost:8848",  # Nacos server address
            # Other optional configuration items
        ),
    )
    # Fetch and return the Agent Card
    return await resolver.get_agent_card()


# %%
# Building an A2A Agent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The ``A2AAgent`` class provided by AgentScope is used to communicate with remote A2A agents, and its usage is similar to regular agents.

agent = A2AAgent(agent_card=agent_card)

# %%
# Using ``A2AAgent``, developers can build chatbot scenario conversations, or encapsulate it as a tool function to build more complex application scenarios such as handoff/router.
# Currently, the format protocol conversion supported by ``A2AAgent`` is handled by ``agentscope.formatter.A2AChatFormatter``, which supports:
#
# - Converting AgentScope's ``Msg`` messages to A2A protocol's ``Message`` format
# - Converting A2A protocol responses back to AgentScope's ``Msg`` format
# - Converting A2A protocol's ``Task`` responses to AgentScope's ``Msg`` format
# - Supporting multiple content types such as text, images, audio, and video
#


async def a2a_in_chatbot() -> None:
    """Example of chatting using A2AAgent."""

    user = UserAgent("user")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


# %%
# Or encapsulate it as a tool function for invocation:


async def create_worker(query: str) -> ToolResponse:
    """Complete a given task through a sub-agent

    Args:
        query (`str`):
            Description of the task to be completed by the sub-agent
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
