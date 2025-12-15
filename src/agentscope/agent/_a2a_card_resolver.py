# -*- coding: utf-8 -*-
"""A2A Agent Card Resolvers for AgentScope.

This module provides various implementations for resolving A2A Agent Cards
from different sources including fixed values, files, and URLs.
"""
from __future__ import annotations

import json
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .._logging import logger

if TYPE_CHECKING:
    from a2a.types import AgentCard
    from v2.nacos.common.client_config import ClientConfig


class AgentCardResolverBase:
    """Base class for A2A Agent Card resolvers.

    This abstract class defines the interface for resolving agent cards
    from various sources (Fixed AgentCard, URL, file, etc.).
    """

    @abstractmethod
    async def get_agent_card(self) -> AgentCard:
        """Get Agent Card from the configured source.

        Returns:
                `AgentCard`:
                        The resolved agent card object.
        """


class FixedAgentCardResolver(AgentCardResolverBase):
    """Agent card resolver that returns a fixed AgentCard."""

    def __init__(self, agent_card: AgentCard) -> None:
        """Initialize the FixedAgentCardResolver.

        Args:
                agent_card (`AgentCard`):
                        The agent card to be used.
        """
        self.agent_card = agent_card

    async def get_agent_card(self) -> AgentCard:
        """Get the fixed agent card.

        Returns:
                `AgentCard`:
                        The fixed agent card.
        """
        return self.agent_card


class FileAgentCardResolver(AgentCardResolverBase):
    """Agent card resolver that loads AgentCard from a JSON file.

    The JSON file should contain an AgentCard object with the following
    required fields:

    - name (str): The name of the agent
    - url (str): The URL of the agent
    - version (str): The version of the agent
    - capabilities (dict): The capabilities of the agent
    - default_input_modes (list[str]): Default input modes
    - default_output_modes (list[str]): Default output modes
    - skills (list): List of agent skills

    Example JSON file content::

        {
            "name": "RemoteAgent",
            "url": "http://localhost:8000",
            "description": "A remote A2A agent",
            "version": "1.0.0",
            "capabilities": {},
            "default_input_modes": ["text/plain"],
            "default_output_modes": ["text/plain"],
            "skills": []
        }
    """

    def __init__(
        self,
        file_path: str,
    ) -> None:
        """Initialize the FileAgentCardResolver.

        Args:
                file_path (`str`):
                        The path to the JSON file containing the agent card.
        """
        self._file_path = file_path

    async def get_agent_card(self) -> AgentCard:
        """Get the agent card from the file.

        Returns:
                `AgentCard`:
                        The agent card loaded from the file.
        """
        return await self._resolve_agent_card()

    async def _resolve_agent_card(self) -> AgentCard:
        from a2a.types import AgentCard

        try:
            path = Path(self._file_path)
            if not path.exists():
                logger.error(
                    "[%s] Agent card file not found: %s",
                    self.__class__.__name__,
                    self._file_path,
                )
                raise FileNotFoundError(
                    f"Agent card file not found: {self._file_path}",
                )
            if not path.is_file():
                logger.error(
                    "[%s] Path is not a file: %s",
                    self.__class__.__name__,
                    self._file_path,
                )
                raise ValueError(f"Path is not a file: {self._file_path}")

            with path.open("r", encoding="utf-8") as f:
                agent_json_data = json.load(f)
                return AgentCard(**agent_json_data)
        except json.JSONDecodeError as e:
            logger.error(
                "[%s] Invalid JSON in agent card file %s: %s",
                self.__class__.__name__,
                self._file_path,
                e,
            )
            raise RuntimeError(
                f"Invalid JSON in agent card file " f"{self._file_path}: {e}",
            ) from e
        except Exception as e:
            logger.error(
                "[%s] Failed to resolve agent card from file %s: %s",
                self.__class__.__name__,
                self._file_path,
                e,
            )
            raise RuntimeError(
                f"Failed to resolve AgentCard from file "
                f"{self._file_path}: {e}",
            ) from e


class WellKnownAgentCardResolver(AgentCardResolverBase):
    """Agent card resolver that loads AgentCard from a well-known URL."""

    def __init__(
        self,
        base_url: str,
        agent_card_path: str | None = None,
    ) -> None:
        """Initialize the WellKnownAgentCardResolver.

        Args:
                base_url (`str`):
                        The base URL to resolve the agent card from.
                agent_card_path (`str | None`, optional):
                        The path to the agent card relative to the base URL.
                        Defaults to AGENT_CARD_WELL_KNOWN_PATH from a2a.utils.
        """
        self._base_url = base_url
        self._agent_card_path = agent_card_path

    async def get_agent_card(self) -> AgentCard:
        """Get the agent card from the well-known URL.

        Returns:
                `AgentCard`:
                        The agent card loaded from the URL.
        """
        import httpx
        from a2a.client import A2ACardResolver
        from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH

        try:
            parsed_url = urlparse(self._base_url)
            if not parsed_url.scheme or not parsed_url.netloc:
                logger.error(
                    "[%s] Invalid URL format: %s",
                    self.__class__.__name__,
                    self._base_url,
                )
                raise ValueError(
                    f"Invalid URL format: {self._base_url}",
                )

            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            relative_card_path = parsed_url.path

            # Use default path if not specified
            agent_card_path = (
                self._agent_card_path
                if self._agent_card_path is not None
                else AGENT_CARD_WELL_KNOWN_PATH
            )

            # Use async context manager to ensure proper cleanup
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout=600),
            ) as _http_client:
                resolver = A2ACardResolver(
                    httpx_client=_http_client,
                    base_url=base_url,
                    agent_card_path=agent_card_path,
                )
                return await resolver.get_agent_card(
                    relative_card_path=relative_card_path,
                )
        except Exception as e:
            logger.error(
                "[%s] Failed to resolve agent card from URL %s: %s",
                self.__class__.__name__,
                self._base_url,
                e,
            )
            raise RuntimeError(
                f"Failed to resolve AgentCard from URL "
                f"{self._base_url}: {e}",
            ) from e


class NacosAgentCardResolver(AgentCardResolverBase):
    """Nacos-based A2A Agent Card resolver.

    Resolves and subscribes to agent cards stored in Nacos A2A registry.
    Supports automatic updates when agent cards change in Nacos.
    """

    def __init__(
        self,
        remote_agent_name: str,
        nacos_client_config: ClientConfig | None = None,
        version: str | None = None,
    ) -> None:
        """Initialize the NacosAgentCardResolver.

        Args:
            remote_agent_name (`str`):
                Name of the remote agent in Nacos.
            nacos_client_config (`ClientConfig | None`, optional):
                Nacos client configuration. If None, reads from environment
                variables:
                - NACOS_SERVER_ADDR: Server address (required if config None)
                - NACOS_USERNAME: Username (optional)
                - NACOS_PASSWORD: Password (optional)
                - NACOS_NAMESPACE_ID: Namespace ID (optional, default: public)
                - NACOS_ACCESS_KEY: Access key (optional)
                - NACOS_SECRET_KEY: Secret key (optional)
                Defaults to None.
            version (`str | None`, optional):
                Version constraint for the agent card.
                Defaults to None.

        Raises:
            ValueError: If remote_agent_name is empty or NACOS_SERVER_ADDR
                is not set when nacos_client_config is None.
        """
        if not remote_agent_name:
            raise ValueError("remote_agent_name is required")

        if nacos_client_config is None:
            nacos_client_config = self._build_config_from_env()

        self._nacos_client_config = nacos_client_config
        self._remote_agent_name = remote_agent_name
        self._version = version

        # Lazy initialization state
        self._initialized = False
        self._nacos_ai_service: Any | None = None
        self._agent_card: AgentCard | None = None

    def _build_config_from_env(self) -> ClientConfig:
        """Build Nacos client config from environment variables.

        Reads the following environment variables:
        - NACOS_SERVER_ADDR: Server address (required)
        - NACOS_USERNAME: Username (optional)
        - NACOS_PASSWORD: Password (optional)
        - NACOS_NAMESPACE_ID: Namespace ID (optional, default: public)
        - NACOS_ACCESS_KEY: Access key (optional)
        - NACOS_SECRET_KEY: Secret key (optional)

        Returns:
            `ClientConfig`:
                The built Nacos client configuration.

        Raises:
            ValueError: If NACOS_SERVER_ADDR is not set.
        """
        import os

        from v2.nacos.common.client_config_builder import ClientConfigBuilder

        server_addr = os.environ.get("NACOS_SERVER_ADDR")
        if not server_addr:
            raise ValueError(
                "NACOS_SERVER_ADDR environment variable is required "
                "when nacos_client_config is not provided",
            )

        builder = ClientConfigBuilder()
        builder.server_address(server_addr)

        # Optional configurations
        username = os.environ.get("NACOS_USERNAME")
        password = os.environ.get("NACOS_PASSWORD")
        if username:
            builder.username(username)
        if password:
            builder.password(password)

        namespace_id = os.environ.get("NACOS_NAMESPACE_ID")
        if namespace_id:
            builder.namespace_id(namespace_id)

        access_key = os.environ.get("NACOS_ACCESS_KEY")
        secret_key = os.environ.get("NACOS_SECRET_KEY")
        if access_key:
            builder.access_key(access_key)
        if secret_key:
            builder.secret_key(secret_key)

        return builder.build()

    async def get_agent_card(self) -> AgentCard:
        """Get agent card from Nacos with lazy initialization.

        Returns:
            `AgentCard`:
                The resolved agent card from Nacos.

        Raises:
            RuntimeError:
                If failed to fetch agent card from Nacos.
        """
        await self._ensure_initialized()

        if self._agent_card is None:
            raise RuntimeError(
                f"Failed to get agent card for {self._remote_agent_name}",
            )

        return self._agent_card

    async def _ensure_initialized(self) -> None:
        """Ensure the resolver is initialized.

        Performs lazy initialization on first call, including:
        - Creating NacosAIService
        - Fetching agent card from Nacos
        - Subscribing to agent card updates
        """
        if self._initialized:
            return

        # Lazy import third-party libraries
        from v2.nacos.ai.model.ai_param import (
            GetAgentCardParam,
            SubscribeAgentCardParam,
        )
        from v2.nacos.ai.nacos_ai_service import NacosAIService

        try:
            logger.debug(
                "[%s] Initializing for agent: %s",
                self.__class__.__name__,
                self._remote_agent_name,
            )

            # Create Nacos AI service
            self._nacos_ai_service = await NacosAIService.create_ai_service(
                self._nacos_client_config,
            )

            # Fetch agent card from Nacos
            self._agent_card = await self._nacos_ai_service.get_agent_card(
                GetAgentCardParam(
                    agent_name=self._remote_agent_name,
                    version=self._version,
                ),
            )

            logger.debug(
                "[%s] Agent card fetched from Nacos: %s",
                self.__class__.__name__,
                self._agent_card.name if self._agent_card else "None",
            )

            # Subscribe to agent card updates
            async def agent_card_subscriber(
                agent_name: str,
                agent_card: AgentCard,
            ) -> None:
                """Callback for agent card updates from Nacos."""
                logger.debug(
                    "[%s] Agent card updated for %s: %s",
                    self.__class__.__name__,
                    agent_name,
                    agent_card.name,
                )
                self._agent_card = agent_card

            await self._nacos_ai_service.subscribe_agent_card(
                SubscribeAgentCardParam(
                    agent_name=self._remote_agent_name,
                    version=self._version,
                    subscribe_callback=agent_card_subscriber,
                ),
            )

            logger.debug(
                "[%s] Subscribed to agent card updates for: %s",
                self.__class__.__name__,
                self._remote_agent_name,
            )

            self._initialized = True

        except Exception as e:
            logger.error(
                "[%s] Failed to initialize Nacos resolver: %s",
                self.__class__.__name__,
                e,
            )
            raise RuntimeError(
                f"Failed to initialize Nacos resolver for "
                f"{self._remote_agent_name}: {e}",
            ) from e
