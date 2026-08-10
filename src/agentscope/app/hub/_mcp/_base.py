# -*- coding: utf-8 -*-
"""The MCP hub base class."""
from abc import ABC, abstractmethod

from ._card import MCPCard, MCPHubPage
from .._base import HubBase


class MCPHubBase(HubBase, ABC):
    """The base class for MCP hub implementations.

    A hub exposes a browsable catalog of :class:`MCPCard` templates.
    Turning a card into a connectable ``MCPClient`` is the installer's
    job, not the hub's — see :func:`agentscope.app._service.render_mcp`.
    """

    @abstractmethod
    async def list_mcps(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> MCPHubPage:
        """Browse the hub catalog.

        Pagination is cursor-based rather than page-numbered: registries
        commonly expose only a forward cursor and no total count, and an
        offset-based hub can always encode its offset into the cursor.

        Args:
            user_id (`str`):
                The user identifier to query MCP cards for.
            q (`str | None`, optional):
                A keyword filtering the cards. When ``None`` the whole
                catalog is browsed.
            cursor (`str | None`, optional):
                The opaque cursor from a previous page. When ``None`` the
                first page is returned.
            limit (`int`, defaults to `20`):
                The maximum number of cards per page.

        Returns:
            `MCPHubPage`:
                The requested page of cards plus the next cursor.
        """

    @abstractmethod
    async def get_mcp(
        self,
        user_id: str,
        card_id: str,
    ) -> MCPCard:
        """Fetch a single card by its hub-local id.

        Args:
            user_id (`str`):
                The user identifier to query the card for.
            card_id (`str`):
                The :attr:`MCPCard.id` addressing the card on this hub.

        Returns:
            `MCPCard`:
                The matching card.

        Raises:
            `KeyError`:
                When this hub has no card under ``card_id``.
        """
