# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiEmbeddingModel."""
from dataclasses import asdict
from tempfile import TemporaryDirectory
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import types
from utils import AnyValue

from agentscope.embedding import (
    GeminiEmbeddingModel,
    EmbeddingResponse,
    EmbeddingUsage,
    FileEmbeddingCache,
)
from agentscope.credential import GeminiCredential
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
        model.parameters = GeminiEmbeddingModel.Parameters()
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

    async def test_sdk_async(self) -> None:
        """Text embedding awaits the asynchronous SDK call."""
        model = self._make_text_model()
        model.client = MagicMock()
        model.client.aio.models.embed_content = AsyncMock(
            return_value=MagicMock(embeddings=[]),
        )
        await model(["hello"])
        model.client.aio.models.embed_content.assert_awaited_once()

    async def test_retry_on_transient_error(self) -> None:
        """A retryable Gemini APIError is retried instead of raised."""
        from google.genai import errors

        model = self._make_text_model()
        model.retry_delay = 0.0
        model.client = MagicMock()
        model.client.aio.models.embed_content = AsyncMock(
            side_effect=[
                errors.APIError(429, {}),
                MagicMock(embeddings=[MagicMock(values=[0.1, 0.2])]),
            ],
        )

        result = await model(["hello"])

        self.assertEqual(result.embeddings, [[0.1, 0.2]])
        self.assertEqual(
            model.client.aio.models.embed_content.await_count,
            2,
        )

    async def test_text_rejects_datablock(self) -> None:
        """Text mode rejects DataBlock inputs."""
        model = self._make_text_model()
        with self.assertRaises(ValueError):
            await GeminiEmbeddingModel._call_text(model, [_img()])

    def _make_configured_model(
        self,
        task_type: str,
        cache: FileEmbeddingCache | None = None,
    ) -> GeminiEmbeddingModel:
        """Use the public constructor with only the SDK client mocked."""
        with patch("google.genai.Client") as client:
            client.return_value.aio.models.embed_content = AsyncMock(
                return_value=MagicMock(
                    embeddings=[MagicMock(values=[0.1, 0.2])],
                ),
            )
            return GeminiEmbeddingModel(
                credential=GeminiCredential(api_key="offline-placeholder"),
                model="gemini-embedding-001",
                dimensions=2,
                parameters=GeminiEmbeddingModel.Parameters(
                    task_type=task_type,
                ),
                embedding_cache=cache,
                max_retries=0,
            )

    async def test_configured_parameters_and_call_precedence(self) -> None:
        """Configured text options reach the SDK; call options win."""
        model = self._make_configured_model("RETRIEVAL_QUERY")
        sdk = model.client.aio.models.embed_content
        for overrides, task_type, dimensions in (
            ({}, "RETRIEVAL_QUERY", 2),
            ({"task_type": "RETRIEVAL_DOCUMENT"}, "RETRIEVAL_DOCUMENT", 2),
            ({"output_dimensionality": 4}, "RETRIEVAL_QUERY", 4),
        ):
            with self.subTest(task_type=task_type, dimensions=dimensions):
                await model(["hello"], **overrides)
                request = sdk.call_args.kwargs
                self.assertEqual(
                    {
                        **request,
                        "config": request["config"].model_dump(
                            exclude_none=True,
                        ),
                    },
                    {
                        "model": "gemini-embedding-001",
                        "contents": ["hello"],
                        "config": {
                            "task_type": task_type,
                            "output_dimensionality": dimensions,
                        },
                    },
                )
                self.assertEqual(
                    model.parameters.model_dump(),
                    {"task_type": "RETRIEVAL_QUERY"},
                )
        self.assertEqual(sdk.await_count, 3)

    async def test_file_cache_uses_effective_parameters(self) -> None:
        """Cache by effective task and dimensions, reusing matching vectors."""
        with TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(cache_dir=directory)
            query = self._make_configured_model("RETRIEVAL_QUERY", cache)
            document = self._make_configured_model("RETRIEVAL_DOCUMENT", cache)
            query.client.aio.models.embed_content.side_effect = [
                MagicMock(embeddings=[MagicMock(values=[0.1, 0.2])]),
                MagicMock(
                    embeddings=[MagicMock(values=[0.1, 0.2, 0.3, 0.4])],
                ),
            ]
            document.client.aio.models.embed_content.return_value = MagicMock(
                embeddings=[MagicMock(values=[0.3, 0.4])],
            )
            cases = (
                (query, {}, "api", [[0.1, 0.2]]),
                (
                    query,
                    {"output_dimensionality": 4},
                    "api",
                    [[0.1, 0.2, 0.3, 0.4]],
                ),
                (
                    query,
                    {"output_dimensionality": 4},
                    "cache",
                    [[0.1, 0.2, 0.3, 0.4]],
                ),
                (document, {}, "api", [[0.3, 0.4]]),
                (
                    query,
                    {"task_type": "RETRIEVAL_DOCUMENT"},
                    "cache",
                    [[0.3, 0.4]],
                ),
                (query, {}, "cache", [[0.1, 0.2]]),
                (
                    query,
                    {"output_dimensionality": 2},
                    "cache",
                    [[0.1, 0.2]],
                ),
            )
            for index, (model, overrides, source, embeddings) in enumerate(
                cases,
            ):
                with self.subTest(case=index):
                    result = await model(["hello"], **overrides)
                    self.assertEqual(
                        asdict(result),
                        {
                            "embeddings": embeddings,
                            "id": A,
                            "created_at": A,
                            "type": "embedding",
                            "usage": {
                                "tokens": None if source == "api" else 0,
                                "time": A if source == "api" else 0,
                                "type": "embedding",
                            },
                            "source": source,
                        },
                    )
            self.assertEqual(
                query.client.aio.models.embed_content.await_count,
                2,
            )
            document.client.aio.models.embed_content.assert_awaited_once()


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

    async def test_multimodal_call_accepts_per_call_dimensions(self) -> None:
        """Multimodal mode forwards the override into the request config."""
        model = self._make_multimodal_model()
        model.client = MagicMock()
        model.client.aio.models.embed_content = AsyncMock(
            return_value=MagicMock(
                embeddings=[MagicMock(values=[0.1, 0.2, 0.3, 0.4])],
            ),
        )
        await model(["hello"], output_dimensionality=4)

        request = model.client.aio.models.embed_content.call_args.kwargs
        self.assertEqual(
            {
                **request,
                "contents": [
                    item.model_dump(exclude_none=True)
                    for item in request["contents"]
                ],
                "config": request["config"].model_dump(exclude_none=True),
            },
            {
                "model": "gemini-embedding-2",
                "contents": [{"parts": [{"text": "hello"}]}],
                "config": {"output_dimensionality": 4},
            },
        )

    async def test_multimodal_uses_embedding_cache(self) -> None:
        """Multimodal results are cached and reused on repeat calls."""
        model = self._make_multimodal_model()
        with TemporaryDirectory() as directory:
            model.embedding_cache = FileEmbeddingCache(cache_dir=directory)
            model.client = MagicMock()
            model.client.aio.models.embed_content = AsyncMock(
                return_value=MagicMock(
                    embeddings=[MagicMock(values=[0.1, 0.2])],
                ),
            )

            first = await model([_img()])
            second = await model([_img()])

        self.assertEqual(first.source, "api")
        self.assertEqual(
            asdict(second),
            {
                "embeddings": [[0.1, 0.2]],
                "id": A,
                "created_at": A,
                "type": "embedding",
                "usage": {"tokens": 0, "time": 0, "type": "embedding"},
                "source": "cache",
            },
        )
        self.assertEqual(
            model.client.aio.models.embed_content.await_count,
            1,
        )

    async def test_multimodal_cache_ignores_http_options(self) -> None:
        """Cache by dimensions while forwarding HTTP options to the SDK."""
        model = self._make_multimodal_model()
        model.dimensions = 2
        short = [0.1, 0.2]
        long = [0.1, 0.2, 0.3, 0.4]
        model.client = MagicMock()
        sdk = AsyncMock(
            side_effect=[
                MagicMock(embeddings=[MagicMock(values=short)]),
                MagicMock(embeddings=[MagicMock(values=long)]),
            ],
        )
        model.client.aio.models.embed_content = sdk
        cases = (
            (types.HttpOptions(timeout=10000), 2, "api", short),
            (types.HttpOptions(timeout=20000), 2, "cache", short),
            ({"timeout": 30000}, 2, "cache", short),
            (types.HttpOptions(timeout=40000), 4, "api", long),
            ({"timeout": 50000}, 4, "cache", long),
        )
        with TemporaryDirectory() as directory:
            model.embedding_cache = FileEmbeddingCache(cache_dir=directory)
            for index, (options, dimensions, source, vector) in enumerate(
                cases,
            ):
                with self.subTest(case=index):
                    result = await model(
                        [_img()],
                        http_options=options,
                        output_dimensionality=dimensions,
                    )
                    self.assertEqual(
                        asdict(result),
                        {
                            "embeddings": [vector],
                            "id": A,
                            "created_at": A,
                            "type": "embedding",
                            "usage": {
                                "tokens": None if source == "api" else 0,
                                "time": A if source == "api" else 0,
                                "type": "embedding",
                            },
                            "source": source,
                        },
                    )
        self.assertEqual(
            [
                call.kwargs["config"].model_dump(exclude_none=True)
                for call in sdk.await_args_list
            ],
            [
                {
                    "http_options": {"timeout": 10000},
                    "output_dimensionality": 2,
                },
                {
                    "http_options": {"timeout": 40000},
                    "output_dimensionality": 4,
                },
            ],
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
