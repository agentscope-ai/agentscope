# -*- coding: utf-8 -*-
"""Credential router test case — payload validation at the API boundary."""
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

VALID_PAYLOAD = {"type": "openai_credential", "api_key": "sk-test"}


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


class CredentialRouterTest(IsolatedAsyncioTestCase):
    """Probe /credential payload validation on a fully started app."""

    def setUp(self) -> None:
        """Start an app against fakeredis backends."""
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
        self._client = self.enterContext(TestClient(app))

    def _create_valid_credential(self) -> str:
        """Store one valid credential and return its id."""
        response = self._client.post(
            "/credential",
            headers=HEADERS,
            json={"data": VALID_PAYLOAD},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["credential_id"]

    def test_create_credential_accepts_valid_payload(self) -> None:
        """A payload matching a registered type is stored (201)."""
        self._create_valid_credential()

    def test_create_credential_rejects_invalid_payload_with_422(
        self,
    ) -> None:
        """Payloads failing the credential union return 422, not 500."""
        for payload in (
            # A known type missing a required field
            {"type": "openai_credential"},
            # An unknown discriminator value
            {"type": "no_such_credential"},
            # No discriminator at all
            {},
        ):
            response = self._client.post(
                "/credential",
                headers=HEADERS,
                json={"data": payload},
            )
            self.assertEqual(
                response.status_code,
                422,
                msg=f"payload {payload} should be a 422",
            )

    def test_update_credential_rejects_invalid_payload_with_422(
        self,
    ) -> None:
        """PATCH payloads failing the credential union return 422."""
        credential_id = self._create_valid_credential()

        response = self._client.patch(
            f"/credential/{credential_id}",
            headers=HEADERS,
            json={"data": {"type": "no_such_credential"}},
        )
        self.assertEqual(response.status_code, 422)

        # The stored credential is untouched by the rejected update:
        # the list still shows the original api_key, not the payload.
        response = self._client.get(
            "/credential",
            headers=HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        records = response.json()["credentials"]
        self.assertEqual(len(records), 1)
        self.assertNotIn("no_such_credential", str(records))
