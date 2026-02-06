# -*- coding: utf-8 -*-
"""The A2A well-known agent card resolver."""
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ._base import AgentCardResolverBase
from .._logging import logger

if TYPE_CHECKING:
    from a2a.types import AgentCard
else:
    AgentCard = "a2a.types.AgentCard"


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
