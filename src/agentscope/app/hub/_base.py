# -*- coding: utf-8 -*-
"""The shared hub identity and lifecycle."""
import re
from typing import Any, Self

# A hub id is a path segment in the hub routes.
HUB_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]+")


class HubBase:
    """The identity and lifecycle shared by every hub implementation.

    A hub is addressed by :attr:`hub_id` in the HTTP routes, and a card
    is addressed globally by the ``(hub_id, card_id)`` pair, so the id
    must stay stable across restarts.

    Hubs are entered once by the app lifespan and live as long as the
    process, so an implementation talking to a registry over HTTP can
    hold one client across requests instead of paying for a fresh
    handshake on every page of a catalog.
    """

    def __init__(
        self,
        hub_id: str,
        display_name: str,
        description: str = "",
        icon_url: str | None = None,
    ) -> None:
        """Initialize the hub identity.

        Args:
            hub_id (`str`):
                The stable identifier addressing this hub. Must match
                ``[a-zA-Z0-9_-]+`` and be unique among the hubs of the
                same kind registered on one app.
            display_name (`str`):
                The user-facing hub name.
            description (`str`, defaults to `""`):
                The user-facing hub description.
            icon_url (`str | None`, optional):
                An image identifying the hub in the picker. ``None``
                leaves the UI to fall back rather than show a gap.

        Raises:
            `ValueError`:
                When ``hub_id`` is not usable as a URL path segment.
        """
        if not HUB_ID_PATTERN.fullmatch(hub_id):
            raise ValueError(
                f"Hub id {hub_id!r} must match [a-zA-Z0-9_-]+ so it is "
                f"usable as a route path segment.",
            )
        self.hub_id = hub_id
        self.display_name = display_name
        self.description = description
        self.icon_url = icon_url

    async def __aenter__(self) -> Self:
        """Enter the hub — open whatever it talks to the registry with.

        The default is a no-op, so a hub with nothing to open needs no
        boilerplate.

        Returns:
            `HubBase`:
                ``self``.
        """
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Exit the hub — close what :meth:`__aenter__` opened."""
