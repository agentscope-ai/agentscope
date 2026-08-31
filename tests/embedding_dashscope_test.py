# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Unit tests for DashScopeEmbeddingModel."""
from dataclasses import asdict
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyValue

from agentscope.credential import DashScopeCredential
from agentscope.embedding import (
    DashScopeEmbeddingModel,
    EmbeddingResponse,
    EmbeddingUsage,
)
from agentscope.message import DataBlock, Base64Source, URLSource

A = AnyValue()


def _text_resp(
    embeddings: list[list[float]],
    total_tokens: int = 10,
    status_code: int = 200,
) -> MagicMock:
    """Build a mock DashScope text embedding response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.output = {"embeddings": [{"embedding": e} for e in embeddings]}
    resp.usage = {"total_tokens": total_tokens}
    return resp


def _cred() -> DashScopeCredential:
    """Create a test credential."""
    return DashScopeCredential(api_key="k")


def _img() -> DataBlock:
    """Create a test image DataBlock."""
    return DataBlock(
        source=Base64Source(data="aWltYWdl", media_type="image/png"),
    )


def _vid() -> DataBlock:
    """Create a test video DataBlock."""
    return DataBlock(
        source=URLSource(url="https://x.com/v.mp4", media_type="video/mp4"),
    )


def _mock_resp(embeddings: list[list[float]]) -> EmbeddingResponse:
    """Create a mock EmbeddingResponse."""
    return EmbeddingResponse(
        embeddings=embeddings,
        usage=EmbeddingUsage(tokens=len(embeddings), time=0.01),
    )


class DashScopeListModelsTest(IsolatedAsyncioTestCase):
    """Test list_models for DashScope."""

    async def test_list_models(self) -> None:
        """Should list 7 models (text + multimodal)."""
        cards = DashScopeEmbeddingModel.list_models()
        names = sorted(c.name for c in cards)
        self.assertEqual(len(cards), 7)
        self.assertIn("text-embedding-v4", names)
        self.assertIn("qwen3-vl-embedding", names)
        self.assertIn("multimodal-embedding-v1", names)

    async def test_hidden_dimensions(self) -> None:
        """multimodal-embedding-v1 declares a fixed dimension (no enum)."""
        cards = DashScopeEmbeddingModel.list_models()
        v1 = next(c for c in cards if c.name == "multimodal-embedding-v1")
        self.assertDictEqual(
            v1.model_dump(),
            {
                "type": "embedding_model",
                "name": "multimodal-embedding-v1",
                "label": "Multimodal Embedding v1",
                "status": "active",
                "input_types": [
                    "text/plain",
                    "image/jpeg",
                    "image/png",
                    "image/bmp",
                ],
                "output_types": ["application/x-embedding"],
                "dimensions": 1024,
                "supported_dimensions": None,
                "context_size": 512,
                "parameter_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "parameter_overrides": {},
            },
        )

    async def test_visible_dimensions(self) -> None:
        """qwen3-vl-embedding exposes dimension choices on the card."""
        cards = DashScopeEmbeddingModel.list_models()
        qwen = next(c for c in cards if c.name == "qwen3-vl-embedding")
        self.assertEqual(qwen.dimensions, 2560)
        self.assertEqual(
            qwen.supported_dimensions,
            [2560, 2048, 1536, 1024, 768, 512, 256],
        )


class DashScopeTextCallTest(IsolatedAsyncioTestCase):
    """Test DashScope text embedding API calls."""

    @patch("dashscope.embeddings.TextEmbedding.call")
    async def test_text_call(self, mock_api: Any) -> None:
        """Text mode returns correct embeddings."""
        mock_api.return_value = _text_resp([[0.1, 0.2], [0.3, 0.4]], 12)
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="text-embedding-v4",
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
                "usage": {"tokens": 12, "time": A, "type": "embedding"},
                "source": "api",
            },
        )

    @patch("dashscope.embeddings.TextEmbedding.call")
    async def test_text_rejects_datablock(self, mock_api: Any) -> None:
        """Text mode rejects DataBlock inputs."""
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="text-embedding-v4",
            dimensions=1024,
        )
        with self.assertRaises(ValueError):
            await model([_img()])

    @patch("dashscope.embeddings.TextEmbedding.call")
    async def test_text_api_error_raises(self, mock_api: Any) -> None:
        """Non-200 status code raises RuntimeError after retries."""
        mock_api.return_value = _text_resp([], status_code=400)
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="text-embedding-v4",
            dimensions=1024,
            retry_delay=0.0,
        )
        with self.assertRaises(RuntimeError):
            await model(["hello"])


class DashScopeMultimodalCallTest(IsolatedAsyncioTestCase):
    """Test DashScope multimodal embedding via mocked _call_multimodal."""

    async def test_multimodal_text_and_image(self) -> None:
        """Multimodal call with text + image."""
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="qwen3-vl-embedding",
            dimensions=2,
        )
        model._call_multimodal = AsyncMock(
            return_value=_mock_resp([[0.1, 0.2], [0.3, 0.4]]),
        )
        result = await model(["describe this", _img()])
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

    async def test_multimodal_batching_by_image_limit(self) -> None:
        """8 images with max_images=5 produces 2 batches (5+3)."""
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="qwen3-vl-embedding",
            dimensions=1,
        )
        call_count = 0

        async def _mock(inputs: list, **_kw: Any) -> EmbeddingResponse:
            nonlocal call_count
            call_count += 1
            return _mock_resp([[0.1]] * len(inputs))

        model._call_multimodal = _mock  # type: ignore[assignment]
        result = await model([_img() for _ in range(8)])
        self.assertEqual(result["embeddings"], [[0.1]] * 8)
        self.assertEqual(call_count, 2)

    async def test_multimodal_batching_by_video_limit(self) -> None:
        """3 videos with max_videos=1 produces 3 batches."""
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="qwen3-vl-embedding",
            dimensions=1,
        )
        call_count = 0

        async def _mock(inputs: list, **_kw: Any) -> EmbeddingResponse:
            nonlocal call_count
            call_count += 1
            return _mock_resp([[0.1]] * len(inputs))

        model._call_multimodal = _mock  # type: ignore[assignment]
        result = await model([_vid(), _vid(), _vid()])
        self.assertEqual(result["embeddings"], [[0.1]] * 3)
        self.assertEqual(call_count, 3)

    async def test_video_base64_rejected(self) -> None:
        """Video with Base64Source raises ValueError."""
        bad = DataBlock(source=Base64Source(data="x", media_type="video/mp4"))
        with self.assertRaises(ValueError):
            DashScopeEmbeddingModel._format_data_block(bad)
