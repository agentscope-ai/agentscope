# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for EmbeddingModelBase."""
from dataclasses import asdict
from typing import Any, Literal
from unittest import TestCase

from utils import AnyString

from agentscope.credential import CredentialBase
from agentscope.embedding import (
    EmbeddingModelBase,
    EmbeddingResponse,
    EmbeddingUsage,
)


class _MinimalEmbedding(EmbeddingModelBase[str]):
    """A concrete ``EmbeddingModelBase`` subclass for exercising the
    base ``__init__`` validation without any provider behaviour."""

    async def _call_api(
        self,
        inputs: list[Any],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Unused: validation is tested before any API call."""
        raise NotImplementedError


def _make(
    *,
    context_size: int = 1024,
    batch_size: int = 16,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> _MinimalEmbedding:
    """Construct an embedding instance with the given knobs."""
    return _MinimalEmbedding(
        credential=CredentialBase(),
        model="test",
        dimensions=8,
        parameters=None,
        context_size=context_size,
        batch_size=batch_size,
        max_retries=max_retries,
        retry_delay=retry_delay,
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
    """Test response merging across cached and API batches."""

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


class EmbeddingModelBaseInitTest(TestCase):
    """Test constructor validation of batching and retry knobs."""

    def test_valid_knobs_are_accepted(self) -> None:
        """Non-negative retry knobs and positive sizes construct fine."""
        model = _make()
        self.assertEqual(model.context_size, 1024)
        self.assertEqual(model.batch_size, 16)
        self.assertEqual(model.max_retries, 3)
        self.assertEqual(model.retry_delay, 1.0)

    def test_zero_retries_and_zero_delay_are_legal(self) -> None:
        """Zero retries (no retry) and zero delay (no sleep) are valid."""
        model = _make(max_retries=0, retry_delay=0.0)
        self.assertEqual(model.max_retries, 0)
        self.assertEqual(model.retry_delay, 0.0)

    def test_non_positive_context_size_raises(self) -> None:
        """``context_size`` must be positive."""
        with self.assertRaises(ValueError):
            _make(context_size=0)
        with self.assertRaises(ValueError):
            _make(context_size=-1)

    def test_non_positive_batch_size_raises(self) -> None:
        """``batch_size`` must be positive — a non-positive value makes
        :meth:`__call__`'s batching step divide-by-zero / loop forever."""
        with self.assertRaises(ValueError):
            _make(batch_size=0)
        with self.assertRaises(ValueError):
            _make(batch_size=-4)

    def test_negative_max_retries_raises(self) -> None:
        """A negative ``max_retries`` empties the retry loop, so reject it."""
        with self.assertRaises(ValueError):
            _make(max_retries=-1)

    def test_negative_retry_delay_raises(self) -> None:
        """A negative ``retry_delay`` breaks backoff, so reject it."""
        with self.assertRaises(ValueError):
            _make(retry_delay=-0.5)
