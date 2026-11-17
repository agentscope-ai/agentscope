"""Tests for the opt-in API-key auth middleware (issue #3314).

Verifies the service-side gate added in response to #3314 (X-User-ID
accepted as identity without verification, CWE-306): when
``AGENTSCOPE_API_KEY`` is set, requests without a valid key are
rejected with 401; when unset, behavior is unchanged.

The middleware unit tests use Starlette TestClient against a minimal
app, avoiding the agentscope app layer (which pulls in Redis and
other heavy deps). ``TestApiKeyAuthWiring`` at the bottom additionally
exercises the real ``create_app`` wiring (fakeredis-backed) — the
mounting logic lives there, and without this test it could silently
disappear while every unit test still passes.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest import mock

import fakeredis.aioredis
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.middleware._api_key_auth import (
    API_KEY_ENV,
    APIKeyAuthMiddleware,
)
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager


def _build_app(api_key: str | None) -> TestClient:
    """Build a minimal Starlette app wrapped with the auth middleware."""

    async def endpoint(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/agent/list", endpoint),
            Route("/health", endpoint),
        ]
    )
    if api_key:
        mw = APIKeyAuthMiddleware(app, api_key=api_key)
        return TestClient(mw)
    # No key configured: build without middleware (auth disabled).
    return TestClient(app)


class TestAPIKeyAuthEnabled(unittest.IsolatedAsyncioTestCase):
    """Auth middleware attached (key configured)."""

    def setUp(self) -> None:
        self.client = _build_app("sk-1")

    def test_valid_key_passes(self) -> None:
        """Auth enabled + correct Bearer key → 200."""
        resp = self.client.get("/agent/list", headers={"Authorization": "Bearer sk-1"})
        self.assertEqual(resp.status_code, 200)

    def test_x_api_key_header_accepted(self) -> None:
        """Auth enabled + X-API-Key header → 200."""
        resp = self.client.get("/agent/list", headers={"X-API-Key": "sk-1"})
        self.assertEqual(resp.status_code, 200)

    def test_missing_key_rejected(self) -> None:
        """Auth enabled + no credentials → 401."""
        resp = self.client.get("/agent/list")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_key_rejected(self) -> None:
        """Auth enabled + wrong key → 401."""
        resp = self.client.get("/agent/list", headers={"X-API-Key": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_health_exempt(self) -> None:
        """/health passes without credentials even when auth is on."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    async def test_non_ascii_credentials_401_not_500(self) -> None:
        """Raw non-ASCII credential bytes -> 401, never a TypeError.

        ``compare_digest``'s str mode rejects non-ASCII input, and a
        raw-socket client can send such bytes (headers decode as
        latin-1). The byte-wise comparison must turn them into a
        plain 401.
        """

        async def downstream(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent: list = []

        async def send(msg):
            sent.append(msg)

        mw = APIKeyAuthMiddleware(downstream, api_key="sk-1")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/agent/list",
            "headers": [(b"x-api-key", "café".encode("latin-1"))],
            "query_string": b"",
        }
        await mw(scope, receive, send)
        self.assertEqual(sent[0]["status"], 401)


class TestAPIKeyAuthDisabled(unittest.IsolatedAsyncioTestCase):
    """No API key configured → auth disabled, requests pass unchanged."""

    def setUp(self) -> None:
        self.client = _build_app(None)

    def test_no_key_requests_pass(self) -> None:
        """Auth disabled → requests pass without credentials."""
        resp = self.client.get("/agent/list")
        self.assertEqual(resp.status_code, 200)

    def test_health_passes(self) -> None:
        """/health passes without credentials."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)


class TestApiKeyAuthWiring(unittest.TestCase):
    """create_app mounts the gate when — and only when — a key is set.

    Anti-regression companion to the middleware unit tests above: the
    mounting lives in ``create_app``, and nothing else would catch it
    silently disappearing while every unit test still passes.
    """

    def _build_app(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class _Storage(RedisStorage):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        class _Bus(RedisMessageBus):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        workdir = tempfile.TemporaryDirectory()
        self.addCleanup(workdir.cleanup)
        return create_app(
            storage=_Storage(),
            message_bus=_Bus(),
            workspace_manager=LocalWorkspaceManager(workdir.name),
            enable_index_worker=False,
        )

    def test_key_set_mounts_gate(self) -> None:
        """Env has a key -> gate in the stack and requests need it."""
        with mock.patch.dict(os.environ, {API_KEY_ENV: "sk-wiring"}):
            app = self._build_app()

        self.assertTrue(
            any(mw.cls is APIKeyAuthMiddleware for mw in app.user_middleware)
        )
        client = TestClient(app)
        # No credentials: the gate answers 401 before routing.
        resp = client.get("/definitely-not-a-route")
        self.assertEqual(resp.status_code, 401)
        # Valid key: the gate passes; the router's 404 is expected.
        resp = client.get(
            "/definitely-not-a-route",
            headers={"Authorization": "Bearer sk-wiring"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_key_unset_leaves_stack_unchanged(self) -> None:
        """Env key empty -> no gate in the stack (backward compatible)."""
        with mock.patch.dict(os.environ, {API_KEY_ENV: ""}):
            app = self._build_app()

        self.assertFalse(
            any(mw.cls is APIKeyAuthMiddleware for mw in app.user_middleware)
        )


if __name__ == "__main__":
    unittest.main()
