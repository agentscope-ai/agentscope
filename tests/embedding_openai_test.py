# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIEmbeddingModel."""
from dataclasses import asdict
import json
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

    async def _check_sdk_usage(
        self,
        include_usage: bool,
        tokens: int | None,
    ) -> None:
        """Exercise SDK parsing with an offline compatible endpoint."""
        import httpx
        import openai

        payload: dict[str, Any] = {
            "object": "list",
            "model": "compatible-embedding",
            "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
            ],
        }
        if include_usage:
            payload["usage"] = (
                {"prompt_tokens": tokens, "total_tokens": tokens}
                if tokens is not None
                else None
            )
        requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/embeddings")
            self.assertEqual(json.loads(request.content)["input"], ["hello"])
            requests.append(request)
            return httpx.Response(200, json=payload)

        async with openai.AsyncClient(
            api_key="test-key",
            base_url="https://embedding.invalid/v1",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handle),
            ),
            max_retries=0,
        ) as client:
            with patch("openai.AsyncClient", return_value=client):
                model = OpenAIEmbeddingModel(
                    credential=OpenAICredential(api_key="test-key"),
                    model="compatible-embedding",
                    dimensions=2,
                    max_retries=0,
                )
            result = await model(["hello"])

        self.assertEqual(len(requests), 1)
        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1, 0.2]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {
                    "tokens": tokens,
                    "time": A,
                    "type": "embedding",
                },
                "source": "api",
            },
        )

    async def test_sdk_omitted_usage(self) -> None:
        """Omitted usage must not discard otherwise valid embeddings."""
        await self._check_sdk_usage(include_usage=False, tokens=None)

    async def test_sdk_null_usage(self) -> None:
        """Explicit null usage leaves the token count unknown."""
        await self._check_sdk_usage(include_usage=True, tokens=None)

    async def test_sdk_reported_usage(self) -> None:
        """Reported token counts are preserved after SDK parsing."""
        await self._check_sdk_usage(include_usage=True, tokens=7)

    async def test_sdk_zero_usage(self) -> None:
        """A reported zero is distinct from an unknown token count."""
        await self._check_sdk_usage(include_usage=True, tokens=0)

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
