"""Regression tests for EmbeddingModelBase._merge_responses source (#2689)."""

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse, EmbeddingUsage


def _resp(source: str, tokens: int = 0) -> EmbeddingResponse:
    return EmbeddingResponse(
        embeddings=[[float(tokens)]],
        usage=EmbeddingUsage(tokens=tokens, time=0.0),
        source=source,
    )


def test_merge_single_batch_preserves_cache_source() -> None:
    merged = EmbeddingModelBase._merge_responses([_resp("cache", 0)])
    assert merged.source == "cache"


def test_merge_all_cache_batches_reports_cache() -> None:
    merged = EmbeddingModelBase._merge_responses(
        [_resp("cache", 0), _resp("cache", 0), _resp("cache", 0)],
    )
    assert merged.source == "cache"
    assert merged.usage.tokens == 0
    assert len(merged.embeddings) == 3


def test_merge_live_api_batches_reports_api() -> None:
    merged = EmbeddingModelBase._merge_responses([_resp("api", 7), _resp("api", 7)])
    assert merged.source == "api"
    assert merged.usage.tokens == 14


def test_merge_mixed_cache_and_api_reports_api() -> None:
    merged = EmbeddingModelBase._merge_responses([_resp("cache", 0), _resp("api", 7)])
    assert merged.source == "api"
    assert merged.usage.tokens == 7
