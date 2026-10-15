# -*- coding: utf-8 -*-
"""Remote-model listing tests — router plumbing and credential helpers,
with the network seam patched out (no I/O)."""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import (
    AnthropicCredential,
    DashScopeCredential,
    GeminiCredential,
    OllamaCredential,
    OpenAICredential,
)

HEADERS = {"X-User-ID": "alice"}

SEAM = "agentscope.credential._base._http_get_json"


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


class CredentialRemoteModelsTest(IsolatedAsyncioTestCase):
    """Probe the remote-models endpoint on a fully started app."""

    def setUp(self) -> None:
        """Start an app with the knowledge base feature left disabled."""
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

    def _create_credential(self, data: dict) -> str:
        """Create a credential through the API and return its id."""
        response = self._client.post(
            "/credential/",
            headers=HEADERS,
            json={"data": data},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["credential_id"]

    async def test_openai_compatible_lists_endpoint_models(self) -> None:
        """The listing sorts, de-duplicates and reports the endpoint's
        model IDs, authenticated with the stored key."""
        credential_id = self._create_credential({
            "type": "openai_credential",
            "name": "demo",
            "api_key": "sk-test",
            "base_url": "https://example.com/v1",
        })

        async def fake_get_json(url: str, headers: dict[str, str]) -> Any:
            self.assertEqual(url, "https://example.com/v1/models")
            self.assertEqual(headers["Authorization"], "Bearer sk-test")
            return {"data": [{"id": "m-b"}, {"id": "m-a"}, {"id": "m-a"}]}

        with patch(SEAM, fake_get_json):
            response = self._client.get(
                f"/credential/{credential_id}/remote-models",
                headers=HEADERS,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"models": ["m-a", "m-b"], "total": 2},
        )

    async def test_endpoint_failure_maps_to_502(self) -> None:
        """A failing remote endpoint surfaces as an upstream error."""

        async def boom(url: str, headers: dict[str, str]) -> Any:
            raise RuntimeError("connection refused")

        credential_id = self._create_credential({
            "type": "openai_credential",
            "name": "demo",
            "api_key": "sk-test",
        })
        with patch(SEAM, boom):
            response = self._client.get(
                f"/credential/{credential_id}/remote-models",
                headers=HEADERS,
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("connection refused", response.json()["detail"])

    async def test_unsupported_type_maps_to_400(self) -> None:
        """Credential types without a remote listing answer 400."""
        credential_id = self._create_credential({
            "type": "gemini_credential",
            "name": "demo",
            "api_key": "key",
        })

        response = self._client.get(
            f"/credential/{credential_id}/remote-models",
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("gemini_credential", response.json()["detail"])

    async def test_missing_credential_maps_to_404(self) -> None:
        """An unknown credential id answers 404 like other routes."""
        response = self._client.get(
            "/credential/nonexistent/remote-models",
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 404)


class CredentialListRemoteModelsTest(IsolatedAsyncioTestCase):
    """Unit-test the per-credential URL and header construction."""

    async def test_dashscope_uses_default_compatible_mode_url(self) -> None:
        """DashScope lists against its compatible-mode base URL."""
        seen: dict[str, Any] = {}

        async def fake_get_json(url: str, headers: dict[str, str]) -> Any:
            seen["url"] = url
            return {"data": [{"id": "qwen-plus"}]}

        credential = DashScopeCredential(api_key="sk-test")
        with patch(SEAM, fake_get_json):
            models = await credential.list_remote_models()

        self.assertEqual(
            seen["url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
        )
        self.assertEqual(models, ["qwen-plus"])

    async def test_anthropic_sends_native_headers(self) -> None:
        """Anthropic lists /v1/models with its native auth headers."""
        seen: dict[str, Any] = {}

        async def fake_get_json(url: str, headers: dict[str, str]) -> Any:
            seen["url"] = url
            seen["headers"] = headers
            return {"data": [{"id": "claude-x"}]}

        credential = AnthropicCredential(api_key="key")
        with patch(SEAM, fake_get_json):
            models = await credential.list_remote_models()

        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/models")
        self.assertEqual(seen["headers"]["x-api-key"], "key")
        self.assertEqual(models, ["claude-x"])

    async def test_ollama_lists_api_tags(self) -> None:
        """Ollama lists /api/tags on the configured host."""
        seen: dict[str, Any] = {}

        async def fake_get_json(url: str, headers: dict[str, str]) -> Any:
            seen["url"] = url
            return {"models": [{"name": "llama3:8b"}]}

        credential = OllamaCredential(host="http://ollama.lan:11434")
        with patch(SEAM, fake_get_json):
            models = await credential.list_remote_models()

        self.assertEqual(seen["url"], "http://ollama.lan:11434/api/tags")
        self.assertEqual(models, ["llama3:8b"])

    async def test_base_default_is_unsupported(self) -> None:
        """Credential types without an implementation answer None."""
        self.assertIsNone(
            await GeminiCredential(api_key="key").list_remote_models(),
        )
