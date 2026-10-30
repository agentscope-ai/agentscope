# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for EmbeddingModelBase."""
from dataclasses import asdict
from typing import Literal
from unittest import TestCase

from utils import AnyString

from agentscope.embedding import (
    EmbeddingModelBase,
    EmbeddingResponse,
    EmbeddingUsage,
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


class EmbeddingMergeUsageTest(TestCase):
    """An incomplete token sum must remain unknown."""

    def test_unknown_tokens_are_not_treated_as_zero(self) -> None:
        """Unknown batches invalidate the total in either position."""
        cases: tuple[list[int | None], ...] = (
            [None, None],
            [7, None],
            [None, 7],
            [3, None, 5],
        )
        for counts in cases:
            with self.subTest(counts=counts):
                result = EmbeddingModelBase._merge_responses(
                    [
                        EmbeddingResponse(
                            embeddings=[[float(index)]],
                            usage=EmbeddingUsage(tokens=count, time=0.25),
                        )
                        for index, count in enumerate(counts)
                    ],
                )
                self.assertIsNone(result.usage.tokens)
                self.assertEqual(len(result.embeddings), len(counts))

    def test_missing_usage_leaves_total_unknown(self) -> None:
        """A batch without a usage object also makes the sum incomplete."""
        for usages in (
            [None, EmbeddingUsage(tokens=7, time=0.5)],
            [EmbeddingUsage(tokens=7, time=0.5), None],
        ):
            with self.subTest(usages=usages):
                result = EmbeddingModelBase._merge_responses(
                    [
                        EmbeddingResponse(embeddings=[[1.0]], usage=usages[0]),
                        EmbeddingResponse(embeddings=[[2.0]], usage=usages[1]),
                    ],
                )
                self.assertIsNone(result.usage.tokens)
                self.assertEqual(result.usage.time, 0.5)
                self.assertEqual(result.embeddings, [[1.0], [2.0]])

    def test_known_tokens_include_real_zeroes(self) -> None:
        """Reported zeroes and cache-hit zeroes can be summed normally."""
        for counts, expected in (([0, 0], 0), ([2, 3], 5), ([0, 4], 4)):
            with self.subTest(counts=counts):
                result = EmbeddingModelBase._merge_responses(
                    [
                        EmbeddingResponse(
                            embeddings=[[float(index)]],
                            usage=EmbeddingUsage(tokens=count, time=0.25),
                            source="cache" if count == 0 else "api",
                        )
                        for index, count in enumerate(counts)
                    ],
                )
                self.assertEqual(result.usage.tokens, expected)
                self.assertEqual(result.usage.time, 0.25)
                self.assertEqual(result.embeddings, [[0.0], [1.0]])
