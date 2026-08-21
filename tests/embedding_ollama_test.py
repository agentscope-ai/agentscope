# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OllamaEmbeddingModel."""
from dataclasses import asdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils import AnyValue

from agentscope.credential import OllamaCredential
from agentscope.embedding import (
    OllamaEmbeddingModel,
    EmbeddingResponse,
    EmbeddingUsage,
)

A = AnyValue()


def _cred() -> OllamaCredential:
    """Create a test credential."""
    return OllamaCredential(host="http://localhost:11434")


def _mock_resp(embeddings: list[list[float]]) -> EmbeddingResponse:
    """Create a mock EmbeddingResponse."""
    return EmbeddingResponse(
        embeddings=embeddings,
        usage=EmbeddingUsage(tokens=len(embeddings), time=0.01),
    )


class OllamaListModelsTest(IsolatedAsyncioTestCase):
    """Test list_models for Ollama."""

    async def test_list_models_empty(self) -> None:
        """Ollama has no pre-defined YAMLs, returns empty list."""
        self.assertEqual(OllamaEmbeddingModel.list_models(), [])


class OllamaEmbeddingCallTest(IsolatedAsyncioTestCase):
    """Test Ollama embedding via mocked _call_api."""

    async def test_basic_call(self) -> None:
        """Basic call returns correct embeddings."""
        model = OllamaEmbeddingModel(
            credential=_cred(),
            model="nomic-embed-text",
            dimensions=2,
        )
        model._call_api = AsyncMock(
            return_value=_mock_resp([[0.1, 0.2], [0.3, 0.4]]),
        )
        result = await model(["hello", "world"])
        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 2, "time": 0.01, "type": "embedding"},
                "source": "api",
            },
        )

    async def test_dimensions_and_host(self) -> None:
        """Dimensions and host are set correctly from constructor."""
        model = OllamaEmbeddingModel(
            credential=OllamaCredential(host="http://gpu:11434"),
            model="test",
            dimensions=768,
        )
        self.assertEqual(model.dimensions, 768)
        self.assertEqual(model.host, "http://gpu:11434")

    async def test_multi_batch(self) -> None:
        """Batching splits inputs correctly."""
        model = OllamaEmbeddingModel(
            credential=_cred(),
            model="test",
            dimensions=1,
        )
        model.batch_size = 2
        call_count = 0

        async def _mock(inputs: list[str], **_kw: Any) -> EmbeddingResponse:
            nonlocal call_count
            call_count += 1
            return _mock_resp([[0.1]] * len(inputs))

        model._call_api = _mock  # type: ignore[assignment]
        result = await model(["a", "b", "c"])
        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1], [0.1], [0.1]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": A, "time": A, "type": "embedding"},
                "source": "api",
            },
        )
        self.assertEqual(call_count, 2)
