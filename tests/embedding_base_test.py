# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for EmbeddingModelBase."""
from dataclasses import asdict
from typing import Any, Literal
from unittest import IsolatedAsyncioTestCase, TestCase

from utils import AnyString

from agentscope.embedding import (
    EmbeddingModelBase,
    EmbeddingResponse,
    EmbeddingUsage,
)
from agentscope.credential import CredentialBase


class _RecordingEmbedding(EmbeddingModelBase[str]):
    """Offline embedding model that records provider calls."""

    def __init__(self, batch_size: int) -> None:
        super().__init__(
            credential=CredentialBase(),
            model="test",
            dimensions=1,
            parameters=None,
            context_size=10,
            batch_size=batch_size,
            max_retries=0,
            retry_delay=0,
        )
        self.calls: list[list[str]] = []

    async def _call_api(
        self,
        inputs: list[str],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        self.calls.append(inputs)
        return EmbeddingResponse(
            embeddings=[[float(value)] for value in inputs],
            usage=EmbeddingUsage(tokens=len(inputs), time=0),
        )


def _resp(
    source: Literal["cache", "api"],
    value: float,
    tokens: int = 0,
    time: float = 0.0,
) -> EmbeddingResponse:
    """Build a batch response with a distinct embedding and usage."""
    return EmbeddingResponse(
        embeddings=[[value]],
        usage=EmbeddingUsage(tokens=tokens, time=time),
        source=source,
    )


class EmbeddingModelBaseTest(TestCase):
    """Test construction and response merging across batches."""

    def test_batch_size_must_be_positive(self) -> None:
        """Reject invalid batches before any provider call can be made."""
        for batch_size in (-1, 0):
            with self.subTest(batch_size=batch_size):
                with self.assertRaisesRegex(
                    ValueError,
                    "batch_size must be a positive integer",
                ):
                    _RecordingEmbedding(batch_size)

    def test_merge_single_batch_preserves_cache_source(self) -> None:
        """Preserve the complete response for a single cached batch."""
        response = _resp("cache", 1.0)

        merged = EmbeddingModelBase._merge_responses([response])

        self.assertEqual(asdict(merged), asdict(response))
        self.assertIs(merged, response)

    def test_merge_all_cache_batches_reports_cache(self) -> None:
        """Merge cached vectors in order with zero API usage."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("cache", 1.0),
                _resp("cache", 2.0),
                _resp("cache", 3.0),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0], [3.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 0, "time": 0.0, "type": "embedding"},
                "source": "cache",
            },
        )

    def test_merge_live_api_batches_uses_max_time(self) -> None:
        """Preserve vector order, sum tokens, and take the maximum time."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("api", 1.0, tokens=7, time=0.25),
                _resp("api", 2.0, tokens=5, time=0.5),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 12, "time": 0.5, "type": "embedding"},
                "source": "api",
            },
        )

    def test_merge_mixed_cache_and_api_reports_api(self) -> None:
        """Report API usage when cached and API batches are combined."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("cache", 1.0),
                _resp("api", 2.0, tokens=7, time=0.5),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 7, "time": 0.5, "type": "embedding"},
                "source": "api",
            },
        )

    def test_merge_empty_responses_reports_api(self) -> None:
        """An empty response list must not count as a cache hit."""
        merged = EmbeddingModelBase._merge_responses([])

        self.assertEqual(
            asdict(merged),
            asdict(
                EmbeddingResponse(
                    embeddings=[],
                    id=AnyString(),
                    created_at=AnyString(),
                    usage=EmbeddingUsage(tokens=0, time=0.0),
                ),
            ),
        )


class EmbeddingModelBaseBatchingTest(IsolatedAsyncioTestCase):
    """Exercise valid batch sizes without contacting a provider."""

    async def test_positive_batch_sizes_preserve_inputs_and_embeddings(
        self,
    ) -> None:
        """Small, partial, and oversized batches keep provider input order."""
        expected_batches = {
            1: [["0"], ["1"], ["2"]],
            2: [["0", "1"], ["2"]],
            5: [["0", "1", "2"]],
        }
        for batch_size, batches in expected_batches.items():
            with self.subTest(batch_size=batch_size):
                model = _RecordingEmbedding(batch_size)

                response = await model(["0", "1", "2"])

                self.assertEqual(model.calls, batches)
                self.assertEqual(
                    response.embeddings,
                    [[0.0], [1.0], [2.0]],
                )

    async def test_empty_inputs_skip_provider_call(self) -> None:
        """An empty request returns empty vectors and zero usage."""
        model = _RecordingEmbedding(batch_size=2)

        response = await model([])

        self.assertEqual(model.calls, [])
        self.assertEqual(response.embeddings, [])
        self.assertEqual(response.usage, EmbeddingUsage(tokens=0, time=0))
