# -*- coding: utf-8 -*-
"""SDK-boundary regressions for configurable multimodal dimensions."""
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel, FileEmbeddingCache


def _response(**kwargs: Any) -> SimpleNamespace:
    """Return deterministic vectors without calling a provider."""
    dimension = kwargs.get("dimension", 2560)
    return SimpleNamespace(
        status_code=200,
        output={
            "embeddings": [
                {"embedding": [0.25] * dimension} for _ in kwargs["input"]
            ],
        },
        usage={"input_tokens": 1, "image_tokens": 0},
    )


def _model(
    dimensions: int,
    name: str = "qwen3-vl-embedding",
    cache: FileEmbeddingCache | None = None,
) -> DashScopeEmbeddingModel:
    """Build a public model wrapper with an offline credential."""
    return DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key="offline-placeholder"),
        model=name,
        dimensions=dimensions,
        embedding_cache=cache,
        max_retries=0,
    )


class DashScopeMultimodalDimensionsTest(IsolatedAsyncioTestCase):
    """Configured dimensions must reach both the SDK and cache identity."""

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_response)
    async def test_qwen_dimensions(self, sdk: Any) -> None:
        """Every advertised dimension is sent to the configurable model."""
        for dimension in (2560, 2048, 1536, 1024, 768, 512, 256):
            with self.subTest(dimension=dimension):
                response = await _model(dimension)(["hello"])
                self.assertEqual(
                    sdk.call_args.kwargs.get("dimension"),
                    dimension,
                )
                self.assertEqual(len(response.embeddings[0]), dimension)

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_response)
    async def test_file_cache_separates_dimensions(self, sdk: Any) -> None:
        """Different contracts must not read each other's stored vectors."""
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(cache_dir=directory)
            small = _model(512, cache=cache)
            large = _model(1024, cache=cache)
            first = await small(["same input"])
            second = await large(["same input"])
            repeated = await small(["same input"])
            self.assertEqual(sdk.call_count, 2)
            self.assertEqual(len(first.embeddings[0]), 512)
            self.assertEqual(len(second.embeddings[0]), 1024)
            self.assertEqual(repeated.source, "cache")
            self.assertEqual(repeated.embeddings, first.embeddings)

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_response)
    async def test_explicit_dimension_override(self, sdk: Any) -> None:
        """An explicit override determines the request and cache identity."""
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(cache_dir=directory)
            first = await _model(1024, cache=cache)(
                ["same input"],
                dimension=512,
                output_type="dense",
            )
            second = await _model(512, cache=cache)(
                ["same input"],
                output_type="dense",
            )
            reordered = await _model(1024, cache=cache)(
                ["same input"],
                output_type="dense",
                dimension=512,
            )
            self.assertEqual(sdk.call_count, 1)
            self.assertEqual(sdk.call_args.kwargs["dimension"], 512)
            self.assertEqual(len(first.embeddings[0]), 512)
            self.assertEqual(second.source, "cache")
            self.assertEqual(second.embeddings, first.embeddings)
            self.assertEqual(reordered.source, "cache")

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_response)
    async def test_fixed_dimension_models(self, sdk: Any) -> None:
        """Fixed models must not receive an unsupported dimension option."""
        for name, dimension in (
            ("tongyi-embedding-vision-plus", 1152),
            ("tongyi-embedding-vision-flash", 768),
            ("multimodal-embedding-v1", 1024),
        ):
            with self.subTest(model=name):
                await _model(dimension, name=name)(["hello"])
                self.assertNotIn("dimension", sdk.call_args.kwargs)
