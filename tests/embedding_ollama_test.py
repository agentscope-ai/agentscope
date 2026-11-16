# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OllamaEmbeddingModel."""
import os
from dataclasses import asdict
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx
import ollama

from utils import AnyValue

from agentscope.credential import OllamaCredential
from agentscope.embedding import (
    FileEmbeddingCache,
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
    """Test Ollama embedding calls and token usage."""

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

    async def test_token_usage(self) -> None:
        """Preserve optional token counts parsed by the real Ollama SDK."""
        cases: list[tuple[dict[str, Any], int | None]] = [
            ({"prompt_eval_count": 8}, 8),
            ({"prompt_eval_count": 0}, 0),
            ({"prompt_eval_count": None}, None),
            ({}, None),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                transport = httpx.MockTransport(
                    Mock(
                        return_value=httpx.Response(
                            200,
                            json={
                                "embeddings": [[0.25, 0.5]],
                                **fields,
                            },
                        ),
                    ),
                )
                client = ollama.AsyncClient(
                    host="http://offline.invalid:11434",
                    transport=transport,
                    trust_env=False,
                )
                self.addAsyncCleanup(client._client.aclose)
                with patch("ollama.AsyncClient", return_value=client):
                    model = OllamaEmbeddingModel(
                        credential=_cred(),
                        model="embeddinggemma",
                        dimensions=2,
                        max_retries=0,
                    )
                response = await model(["one"])
                self.assertDictEqual(
                    asdict(response),
                    {
                        "embeddings": [[0.25, 0.5]],
                        "id": A,
                        "created_at": A,
                        "type": "embedding",
                        "usage": {
                            "tokens": expected,
                            "time": A,
                            "type": "embedding",
                        },
                        "source": "api",
                    },
                )


class OllamaEmbeddingCacheHostTest(IsolatedAsyncioTestCase):
    """The embedding cache must not be shared across Ollama hosts."""

    def _assert_response(
        self,
        response: EmbeddingResponse,
        embeddings: list[list[float]],
        source: str,
    ) -> None:
        """Assert the complete embedding response structure."""
        usage_value = 0 if source == "cache" else A
        self.assertDictEqual(
            asdict(response),
            {
                "embeddings": embeddings,
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {
                    "tokens": usage_value,
                    "time": usage_value,
                    "type": "embedding",
                },
                "source": source,
            },
        )

    def _make_model(
        self,
        cache: FileEmbeddingCache,
        host: str | None,
        vector: list[float],
    ) -> OllamaEmbeddingModel:
        """Create an Ollama model with a mocked embedding response."""
        model = OllamaEmbeddingModel(
            credential=OllamaCredential(host=host),
            model="embed",
            dimensions=2,
            embedding_cache=cache,
        )
        self.addAsyncCleanup(model.client._client.aclose)
        model.client = SimpleNamespace(
            embed=AsyncMock(
                return_value=SimpleNamespace(embeddings=[vector]),
            ),
        )
        return model

    async def test_cache_is_isolated_by_host(self) -> None:
        """Two hosts serving the same model name must not reuse each
        other's cached vectors."""
        with TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir)
            model_a = self._make_model(
                cache,
                "http://host-a:11434",
                [1.0, 0.0],
            )
            model_b = self._make_model(
                cache,
                "http://host-b:11434",
                [0.0, 1.0],
            )

            res_a = await model_a(["hello"])
            res_b = await model_b(["hello"])

            self._assert_response(res_a, [[1.0, 0.0]], "api")
            self._assert_response(res_b, [[0.0, 1.0]], "api")
            model_b.client.embed.assert_awaited_once()
            # The endpoint must stay out of the Ollama request payload.
            self.assertNotIn(
                "endpoint",
                model_b.client.embed.await_args.kwargs,
            )

    async def test_cache_uses_host_from_environment(self) -> None:
        """The resolved OLLAMA_HOST must isolate cached vectors."""
        with TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir)
            with patch.dict(
                os.environ,
                {"OLLAMA_HOST": "http://host-a:11434"},
            ):
                model_a = self._make_model(cache, None, [1.0, 0.0])
            with patch.dict(
                os.environ,
                {"OLLAMA_HOST": "http://host-b:11434"},
            ):
                model_b = self._make_model(cache, None, [0.0, 1.0])
            with patch.dict(
                os.environ,
                {"OLLAMA_HOST": "http://host-a:11434/"},
            ):
                model_a_again = self._make_model(cache, None, [0.5, 0.5])

            res_a = await model_a(["hello"])
            res_b = await model_b(["hello"])
            res_a_again = await model_a_again(["hello"])

            self.assertEqual(model_a._cache_endpoint, "http://host-a:11434")
            self.assertEqual(model_b._cache_endpoint, "http://host-b:11434")
            self._assert_response(res_a, [[1.0, 0.0]], "api")
            self._assert_response(res_b, [[0.0, 1.0]], "api")
            model_b.client.embed.assert_awaited_once()
            self._assert_response(res_a_again, [[1.0, 0.0]], "cache")
            model_a_again.client.embed.assert_not_awaited()
