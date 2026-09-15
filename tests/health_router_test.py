# -*- coding: utf-8 -*-
"""Health router test case — readiness reporting, without any I/O."""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}


def _fake_backends() -> tuple:
    """Build a fakeredis-backed storage and message bus."""
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

    return _Storage(), _Bus()


class HealthRouterTest(IsolatedAsyncioTestCase):
    """Probe the health endpoint on a fully started app."""

    def setUp(self) -> None:
        """Start an app with the knowledge base feature left disabled."""
        # enterContext is the unittest-native way to bind a context
        # manager to the test's lifetime; pylint does not recognise it.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_backends()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
        )
        self._app = app
        self._client = self.enterContext(TestClient(app))

    def test_health_reports_ok_once_started(self) -> None:
        """A fully started app reports ok with its configured version."""
        response = self._client.get("/health", headers=HEADERS)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], self._app.version)

        # Both the eagerly attached and the lifespan-built components
        # must be present, otherwise "ok" would be meaningless.
        self.assertEqual(body["components"]["storage"], "ok")
        self.assertEqual(body["components"]["chat_service"], "ok")

    def test_health_requires_user_id(self) -> None:
        """The endpoint is not exempt from the X-User-ID requirement."""
        response = self._client.get("/health")

        self.assertEqual(response.status_code, 422)

    def test_disabled_features_do_not_break_health(self) -> None:
        """An unconfigured optional feature reads as disabled, not down."""
        body = self._client.get("/health", headers=HEADERS).json()

        self.assertEqual(body["components"]["knowledge_base"], "disabled")
        self.assertEqual(body["components"]["mcp_hubs"], "disabled")
        self.assertEqual(body["status"], "ok")

    def test_missing_lifespan_reports_not_ready(self) -> None:
        """Mounting without running the lifespan reports 503 with detail.

        This is the deployment mistake the endpoint exists to catch —
        Starlette skips a mounted sub-app's lifespan, so nothing the
        lifespan builds is ever attached.
        """
        with tempfile.TemporaryDirectory() as workdir:
            storage, bus = _fake_backends()
            app = create_app(
                storage=storage,
                message_bus=bus,
                workspace_manager=LocalWorkspaceManager(workdir),
                enable_index_worker=False,
            )
            # No `with`: the lifespan never runs, mimicking a bare mount.
            response = TestClient(app).get("/health", headers=HEADERS)

        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["components"]["chat_service"], "not_ready")
        # Eagerly attached state survives — the report pinpoints the gap.
        self.assertEqual(body["components"]["storage"], "ok")
