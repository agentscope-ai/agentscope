# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIEmbeddingModel."""
from dataclasses import asdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyValue

from agentscope.credential import OpenAICredential
from agentscope.embedding import OpenAIEmbeddingModel

A = AnyValue()


def _make_response(
    embeddings: list[list[float]],
    total_tokens: int = 10,
) -> MagicMock:
    """Build a mock ``openai.embeddings.create`` response."""
    resp = MagicMock()
    resp.data = [MagicMock(embedding=e) for e in embeddings]
    resp.usage = MagicMock(total_tokens=total_tokens)
    return resp


class OpenAIListModelsTest(IsolatedAsyncioTestCase):
    """Test ``list_models()`` for OpenAI."""

    async def test_list_models(self) -> None:
        """Should list 2 models with correct parameter_schema."""
        cards = OpenAIEmbeddingModel.list_models()
        names = sorted(c.name for c in cards)
        self.assertEqual(
            names,
            ["text-embedding-3-large", "text-embedding-3-small"],
        )

        card = next(c for c in cards if c.name == "text-embedding-3-small")
        self.assertDictEqual(
            card.model_dump(),
            {
                "type": "embedding_model",
                "name": "text-embedding-3-small",
                "label": "Text Embedding 3 Small",
                "status": "active",
                "input_types": ["text/plain"],
                "output_types": ["application/x-embedding"],
                "dimensions": 1536,
                "supported_dimensions": [1536, 1024, 768, 512, 256],
                "context_size": 8191,
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "parameter_overrides": {},
            },
        )


class OpenAIEmbeddingCallTest(IsolatedAsyncioTestCase):
    """Test OpenAI embedding API calls with mocked responses."""

    @patch("openai.AsyncClient")
    async def test_single_batch(self, mock_client_cls: Any) -> None:
        """Single batch call returns correct embeddings."""
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=_make_response([[0.1, 0.2], [0.3, 0.4]], 8),
        )
        mock_client_cls.return_value = mock_client

        model = OpenAIEmbeddingModel(
            credential=OpenAICredential(api_key="k"),
            model="text-embedding-3-small",
            dimensions=2,
        )
        result = await model(["hello", "world"])

        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 8, "time": A, "type": "embedding"},
                "source": "api",
            },
        )

    @patch("openai.AsyncClient")
    async def test_multi_batch(self, mock_client_cls: Any) -> None:
        """Inputs exceeding batch_size are split and merged."""
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=[
                _make_response([[0.1], [0.2]], 4),
                _make_response([[0.3]], 2),
            ],
        )
        mock_client_cls.return_value = mock_client

        model = OpenAIEmbeddingModel(
            credential=OpenAICredential(api_key="k"),
            model="text-embedding-3-small",
            dimensions=1,
        )
        model.batch_size = 2

        result = await model(["a", "b", "c"])

        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1], [0.2], [0.3]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 6, "time": A, "type": "embedding"},
                "source": "api",
            },
        )

    @patch("openai.AsyncClient")
    async def test_empty_input(self, mock_client_cls: Any) -> None:
        """Empty input returns empty response without API call."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        model = OpenAIEmbeddingModel(
            credential=OpenAICredential(api_key="k"),
            model="text-embedding-3-small",
            dimensions=1536,
        )
        result = await model([])

        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 0, "time": 0, "type": "embedding"},
                "source": "api",
            },
        )
        mock_client.embeddings.create.assert_not_called()

    @patch("openai.AsyncClient")
    async def test_retry_on_transient_error(
        self,
        mock_client_cls: Any,
    ) -> None:
        """Retryable OpenAI errors are retried."""
        import openai

        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=[
                openai.RateLimitError(
                    message="rate limit",
                    response=MagicMock(status_code=429),
                    body=None,
                ),
                _make_response([[0.1]], 1),
            ],
        )
        mock_client_cls.return_value = mock_client

        model = OpenAIEmbeddingModel(
            credential=OpenAICredential(api_key="k"),
            model="text-embedding-3-small",
            dimensions=1,
            retry_delay=0.0,
        )
        result = await model(["hello"])

        self.assertEqual(result["embeddings"], [[0.1]])
        self.assertEqual(mock_client.embeddings.create.await_count, 2)
