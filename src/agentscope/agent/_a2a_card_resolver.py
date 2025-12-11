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
