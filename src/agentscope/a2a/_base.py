# -*- coding: utf-8 -*-
"""The A2A agent card resolver base class."""
from abc import abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from a2a.types import AgentCard
else:
    AgentCard = "a2a.types.AgentCard"


class AgentCardResolverBase:
    """Base class for A2A agent card resolvers, responsible for fetching
    agent cards from various sources. Implementations must provide the
    `get_agent_card` method to retrieve the agent card.
    """

    @abstractmethod
    async def get_agent_card(self, *args: Any, **kwargs: Any) -> AgentCard:
        """Get Agent Card from the configured source.

        Returns:
            `AgentCard`:
                The resolved agent card object.
        """
