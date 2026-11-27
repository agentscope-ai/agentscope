# -*- coding: utf-8 -*-
"""Channel routes must map service 404s to HTTP errors, not 500s.

When two concurrent requests target the same channel, both can pass the
route-level ownership check before the first one deletes the channel.
The service then raises a 404 ``ChannelError`` for the second request
(#4114); the routes convert it the same way ``update_channel`` does,
instead of letting it surface as an unhandled 500.
"""
from types import SimpleNamespace
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router._channel import channel_router
from agentscope.app.channel._errors import ChannelError
from agentscope.app.deps import (
    get_channel_service,
    get_current_user_id,
    get_storage,
)

_USER = "u1"


class _OwnedStorage:
    """Storage where the route-level ownership check passes."""

    async def get_channel(self, channel_id: str) -> object:
        """Return a record owned by the test user."""
        _ = channel_id
        return SimpleNamespace(user_id=_USER)


class _LostRaceService:
    """Service stub where a concurrent delete already won."""

    async def delete(self, channel_id: str) -> None:
        """Report the channel as deleted by the other request."""
        raise ChannelError(f"Channel '{channel_id}' not found.", 404)

    async def set_enabled(self, channel_id: str, enabled: bool) -> None:
        """Report the channel as deleted by the other request."""
        _ = enabled
        raise ChannelError(f"Channel '{channel_id}' not found.", 404)


class _QuietService:
    """Service stub where the requested action succeeds."""

    async def delete(self, channel_id: str) -> None:
        """Accept the deletion."""

    async def set_enabled(self, channel_id: str, enabled: bool) -> None:
        """Accept the state change."""


def _client(service: object) -> TestClient:
    """Build an app around the channel router and the given service."""
    app = FastAPI()
    app.include_router(channel_router)
    app.dependency_overrides[get_storage] = _OwnedStorage
    app.dependency_overrides[get_channel_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: _USER
    return TestClient(app)


class LostRaceTest(TestCase):
    """A lost race must surface as 404, not an unhandled error."""

    def test_second_delete_returns_404(self) -> None:
        """A delete overlapping another delete maps the error to 404."""
        response = _client(_LostRaceService()).delete("/channels/c1")

        self.assertEqual(response.status_code, 404)

    def test_enable_after_delete_returns_404(self) -> None:
        """Enabling a just-deleted channel maps the error to 404."""
        response = _client(_LostRaceService()).post("/channels/c1/enable")

        self.assertEqual(response.status_code, 404)

    def test_disable_after_delete_returns_404(self) -> None:
        """Disabling a just-deleted channel maps the error to 404."""
        response = _client(_LostRaceService()).post("/channels/c1/disable")

        self.assertEqual(response.status_code, 404)

    def test_delete_success_keeps_204(self) -> None:
        """The happy path still answers 204 with no content."""
        response = _client(_QuietService()).delete("/channels/c1")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
