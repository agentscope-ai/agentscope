# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiEmbeddingModel."""
from dataclasses import asdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils import AnyValue

from agentscope.embedding import (
    GeminiEmbeddingModel,
    EmbeddingResponse,
    EmbeddingUsage,
)
from agentscope.message import DataBlock, Base64Source

A = AnyValue()


def _mock_resp(embeddings: list[list[float]]) -> EmbeddingResponse:
    """Create a mock EmbeddingResponse."""
    return EmbeddingResponse(
        embeddings=embeddings,
        usage=EmbeddingUsage(tokens=len(embeddings), time=0.01),
    )


def _img() -> DataBlock:
    """Create a test image DataBlock."""
    return DataBlock(
        source=Base64Source(data="aWltYWdl", media_type="image/png"),
    )


class GeminiListModelsTest(IsolatedAsyncioTestCase):
    """Test list_models for Gemini."""

    async def test_list_models(self) -> None:
        """Should list 2 models."""
        cards = GeminiEmbeddingModel.list_models()
        names = sorted(c.name for c in cards)
        self.assertEqual(names, ["gemini-embedding-001", "gemini-embedding-2"])

    async def test_text_model_card(self) -> None:
        """gemini-embedding-001 is text-only with 2048 context."""
        cards = GeminiEmbeddingModel.list_models()
        card = next(c for c in cards if c.name == "gemini-embedding-001")
        self.assertDictEqual(
            card.model_dump(),
            {
                "type": "embedding_model",
                "name": "gemini-embedding-001",
                "label": "Gemini Embedding 001",
                "status": "active",
                "input_types": ["text/plain"],
                "output_types": ["application/x-embedding"],
                "dimensions": 3072,
                "supported_dimensions": [3072, 1536, 768, 512, 256, 128],
                "context_size": 2048,
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "parameter_overrides": {},
            },
        )

    async def test_multimodal_model_card(self) -> None:
        """gemini-embedding-2 is multimodal with 8192 context."""
        cards = GeminiEmbeddingModel.list_models()
        card = next(c for c in cards if c.name == "gemini-embedding-2")
        self.assertIn("image/png", card.input_types)
        self.assertIn("application/pdf", card.input_types)
        self.assertEqual(card.context_size, 8192)
        self.assertEqual(card.supported_dimensions, [3072, 1536, 768])


class GeminiTextCallTest(IsolatedAsyncioTestCase):
    """Test Gemini text embedding via mocked _call_text."""

    def _make_text_model(self) -> GeminiEmbeddingModel:
        """Create a text-mode model bypassing __init__ (no genai)."""
        model = GeminiEmbeddingModel.__new__(GeminiEmbeddingModel)
        model.model = "gemini-embedding-001"
        model.dimensions = 2
        model.context_size = 2048
        model.batch_size = 100
        model.max_retries = 3
        model.retry_delay = 1.0
        model._is_multimodal = False
        model.embedding_cache = None
        return model

    async def test_text_call(self) -> None:
        """Text mode delegates to _call_text."""
        model = self._make_text_model()
        model._call_text = AsyncMock(
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

    async def test_text_rejects_datablock(self) -> None:
        """Text mode rejects DataBlock inputs."""
        model = self._make_text_model()
        with self.assertRaises(ValueError):
            await GeminiEmbeddingModel._call_text(model, [_img()])


class GeminiMultimodalCallTest(IsolatedAsyncioTestCase):
    """Test Gemini multimodal embedding via mocked _call_multimodal."""

    def _make_multimodal_model(self) -> GeminiEmbeddingModel:
        """Create a multimodal-mode model bypassing __init__."""
        model = GeminiEmbeddingModel.__new__(GeminiEmbeddingModel)
        model.model = "gemini-embedding-2"
        model.dimensions = 1
        model.context_size = 8192
        model.batch_size = 100
        model.max_retries = 3
        model.retry_delay = 1.0
        model._is_multimodal = True
        model.embedding_cache = None
        from agentscope.embedding._gemini._model import _MultimodalLimits

        model._limits = _MultimodalLimits(
            max_elements=20,
            max_images=6,
            max_videos=1,
            max_audios=1,
            max_pdfs=1,
        )
        return model

    async def test_multimodal_delegates(self) -> None:
        """Multimodal mode delegates to _call_multimodal."""
        model = self._make_multimodal_model()
        model._call_multimodal = AsyncMock(
            return_value=_mock_resp([[0.1], [0.2]]),
        )
        result = await model(["hello", "world"])
        self.assertDictEqual(
            asdict(result),
            {
                "embeddings": [[0.1], [0.2]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 2, "time": 0.01, "type": "embedding"},
                "source": "api",
            },
        )

    async def test_multimodal_batching_by_image_limit(self) -> None:
        """8 images with max_images=6 produces 2 batches (6+2)."""
        model = self._make_multimodal_model()
        call_count = 0

        async def _mock(inputs: list, **_kw: Any) -> EmbeddingResponse:
            nonlocal call_count
            call_count += 1
            return _mock_resp([[0.1]] * len(inputs))

        model._call_multimodal = _mock  # type: ignore[assignment]
        result = await model([_img() for _ in range(8)])
        self.assertEqual(result["embeddings"], [[0.1]] * 8)
        self.assertEqual(call_count, 2)
