# -*- coding: utf-8 -*-
"""The Nacos-based A2A Agent Card resolver."""

from typing import TYPE_CHECKING

from ._base import AgentCardResolverBase
from .._logging import logger

if TYPE_CHECKING:
    from a2a.types import AgentCard
    from v2.nacos.common.client_config import ClientConfig
else:
    AgentCard = "a2a.types.AgentCard"
    ClientConfig = "v2.nacos.common.client_config.ClientConfig"


class NacosAgentCardResolver(AgentCardResolverBase):
    """Nacos-based A2A Agent Card resolver.

    Nacos is a dynamic service discovery, configuration and service
    management platform for building cloud native applications. This resolver
    fetches the agent card from a Nacos server and subscribes to updates.
    """

    def __init__(
        self,
        remote_agent_name: str,
        nacos_client_config: ClientConfig,
        version: str | None = None,
    ) -> None:
        """Initialize the nacos agent card resolver.

        Args:
            remote_agent_name (`str`):
                Name of the remote agent in Nacos.
            nacos_client_config (`ClientConfig | None`, optional):
                Nacos client configuration, where a `server_addresses`
                parameter is required.
            version (`str | None`, optional):
                Version of the agent card to fetch. If None, fetches the
                latest version. This version is also used when subscribing
                to agent card updates.
                Defaults to None (latest version).
        """
        if not remote_agent_name:
            raise ValueError(
                "The remote_agent_name cannot be empty.",
            )

        if not nacos_client_config:
            raise ValueError(
                "The nacos_client_config cannot be None.",
            )

        self._nacos_client_config = nacos_client_config
        self._remote_agent_name = remote_agent_name
        self._version = version

    async def get_agent_card(self) -> AgentCard:
        """Get agent card from Nacos with lazy initialization.

        Returns:
            `AgentCard`:
                The resolved agent card from Nacos.
        """
        try:
            from v2.nacos.ai.model.ai_param import GetAgentCardParam
            from v2.nacos.ai.nacos_ai_service import NacosAIService
        except ImportError as e:
            raise ImportError(
                "Please install the nacos sdk by running `pip install "
                "nacos-sdk-python>=3.0.0` first.",
            ) from e

        client = None
        try:
            client = await NacosAIService.create_ai_service(
                self._nacos_client_config,
            )

            await client.start()
            return await client.get_agent_card(
                GetAgentCardParam(
                    agent_name=self._remote_agent_name,
                    version=self._version,
                ),
            )

        finally:
            if client:
                # Close the Nacos client to free resources
                try:
                    await client.shutdown()
                except Exception as e:
                    logger.warning(
                        "Failed to shutdown Nacos client: %s",
                        str(e),
                    )
