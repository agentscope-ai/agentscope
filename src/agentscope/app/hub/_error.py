# -*- coding: utf-8 -*-
"""The failure shared by every hub."""


class HubError(Exception):
    """Raised when a hub's upstream registry returns a failure.

    One type for every hub: the registries differ but the failure does
    not, and a caller that wants to map an upstream 429 onto a 503 has
    to catch something. ``hub_id`` says which hub failed, so a page
    listing several of them can name the one at fault.
    """

    def __init__(self, hub_id: str, status_code: int, message: str) -> None:
        """Initialize the error.

        Args:
            hub_id (`str`):
                The hub whose upstream failed.
            status_code (`int`):
                The HTTP status code the registry returned.
            message (`str`):
                The response body, surfaced verbatim — public registry
                errors are plain text and worth showing.
        """
        self.hub_id = hub_id
        self.status_code = status_code
        self.message = message
        super().__init__(f"{hub_id} returned {status_code}: {message}")
