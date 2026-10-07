# -*- coding: utf-8 -*-
"""Custom model-card directory tests — catalog merging, router exposure
and runtime card lookup, all offline."""
import tempfile
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import ChatModelConfig, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.model import OpenAIChatModel

HEADERS = {"X-User-ID": "alice"}

CHAT_CARD = """\
name: custom-chat-model
label: Custom Chat Model
status: active

input_types:
  - text/plain

output_types:
  - text/plain
  - application/x-thinking

context_size: 7777
output_size: 2048
"""

OVERRIDE_CARD = """\
name: gpt-4.1-mini
label: GPT-4.1 Mini (refined)
status: active

input_types:
  - text/plain

output_types:
  - text/plain

context_size: 123456
output_size: 4096
"""

EMBEDDING_CARD = """\
type: embedding_model
name: custom-embedding
label: Custom Embedding
status: active

input_types:
  - text/plain

output_types:
  - application/x-embedding

context_size: 4096
dimensions: 1536
"""


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


def _make_card_tree(root: Path) -> None:
    """Write a chat / embedding card tree under ``root``."""
    chat_dir = root / "chat"
    embedding_dir = root / "embedding"
    chat_dir.mkdir(parents=True)
    embedding_dir.mkdir(parents=True)
    (chat_dir / "custom-chat-model.yaml").write_text(CHAT_CARD, encoding="utf-8")
    (chat_dir / "gpt-4.1-mini.yaml").write_text(OVERRIDE_CARD, encoding="utf-8")
    (embedding_dir / "custom-embedding.yaml").write_text(
        EMBEDDING_CARD,
        encoding="utf-8",
    )


class ModelCardDirsRouterTest(IsolatedAsyncioTestCase):
    """Catalog endpoints include cards from the configured directories."""

    def setUp(self) -> None:
        """Start an app with an extra card directory configured."""
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        cards = Path(workdir) / "cards"
        _make_card_tree(cards)
        storage, bus = _fake_backends()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
            model_card_dirs={"openai_credential": cards},
        )
        self._app = app
        self._client = self.enterContext(TestClient(app))

    def test_chat_catalog_includes_custom_and_override(self) -> None:
        """Custom cards appear; a same-name card overrides the built-in."""
        body = self._client.get(
            "/model/",
            params={"provider": "openai_credential"},
            headers=HEADERS,
        ).json()

        names = [model["name"] for model in body["models"]]
        self.assertIn("custom-chat-model", names)
        override = next(m for m in body["models"] if m["name"] == "gpt-4.1-mini")
        self.assertEqual(override["context_size"], 123456)
        self.assertEqual(override["label"], "GPT-4.1 Mini (refined)")

    def test_embedding_catalog_includes_custom(self) -> None:
        """Cards under the embedding/ subdirectory reach the embedding
        catalog."""
        body = self._client.get(
            "/embedding-model/",
            params={"provider": "openai_credential"},
            headers=HEADERS,
        ).json()

        names = [model["name"] for model in body["models"]]
        self.assertIn("custom-embedding", names)

    def test_other_credential_types_unaffected(self) -> None:
        """A directory keyed to one credential type leaks nowhere else."""
        body = self._client.get(
            "/model/",
            params={"provider": "dashscope_credential"},
            headers=HEADERS,
        ).json()

        names = [model["name"] for model in body["models"]]
        self.assertNotIn("custom-chat-model", names)


class ModelCardDirsRuntimeTest(IsolatedAsyncioTestCase):
    """Session model construction honors cards from the directories."""

    def setUp(self) -> None:
        """Start an app and store one OpenAI credential."""
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        cards = Path(workdir) / "cards"
        _make_card_tree(cards)
        storage, bus = _fake_backends()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
            model_card_dirs={"openai_credential": cards},
        )
        self._app = app
        self._client = self.enterContext(TestClient(app))
        response = self._client.post(
            "/credential/",
            headers=HEADERS,
            json={"data": {
                "type": "openai_credential",
                "name": "demo",
                "api_key": "sk-test",
            }},
        )
        self._credential_id = response.json()["credential_id"]

    async def test_get_model_applies_custom_card_metadata(self) -> None:
        """A custom card supplies the model's context size and inputs."""
        from agentscope.app._service import get_model

        model = await get_model(
            "alice",
            ChatModelConfig(
                type="openai",
                credential_id=self._credential_id,
                model="custom-chat-model",
                parameters={},
            ),
            self._app.state.resource_access_service,
            model_card_dirs=self._app.state.model_card_dirs,
        )

        self.assertIsInstance(model, OpenAIChatModel)
        self.assertEqual(model.context_size, 7777)
        self.assertEqual(
            model.formatter.input_types,
            ["text/plain"],
        )

    async def test_get_model_falls_back_without_card(self) -> None:
        """A model name with no card anywhere keeps the defaults."""
        from agentscope.app._service import get_model

        model = await get_model(
            "alice",
            ChatModelConfig(
                type="openai",
                credential_id=self._credential_id,
                model="totally-unknown-model",
                parameters={},
            ),
            self._app.state.resource_access_service,
            model_card_dirs=self._app.state.model_card_dirs,
        )

        self.assertIsNotNone(model)
        self.assertNotEqual(model.context_size, 7777)
