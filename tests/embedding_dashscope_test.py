# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Unit tests for DashScopeEmbeddingModel."""
import tempfile
from dataclasses import asdict
from threading import get_ident
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call, patch

from utils import AnyValue

from agentscope.credential import DashScopeCredential
from agentscope.embedding import (
    DashScopeEmbeddingModel,
    EmbeddingResponse,
    EmbeddingUsage,
    FileEmbeddingCache,
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


def _multimodal_resp(**kwargs: Any) -> SimpleNamespace:
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


def _multimodal_model(
    dimensions: int,
    name: str,
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


class DashScopeListModelsTest(IsolatedAsyncioTestCase):
    """Test list_models for DashScope."""

    def test_multimodal_capability(self) -> None:
        """Instances expose the multimodal capability of their model cards."""
        self.assertDictEqual(
            {
                card.name: DashScopeEmbeddingModel(
                    credential=_cred(),
                    model=card.name,
                    dimensions=card.dimensions,
                ).supports_multimodal
                for card in DashScopeEmbeddingModel.list_models()
            },
            {
                "text-embedding-v4": False,
                "tongyi-embedding-vision-flash": True,
                "qwen3-vl-embedding": True,
                "qwen2.5-vl-embedding": True,
                "text-embedding-v3": False,
                "multimodal-embedding-v1": True,
                "tongyi-embedding-vision-plus": True,
            },
        )

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
    async def test_text_api_key_override_with_cache(
        self,
        mock_api: Any,
    ) -> None:
        """Use the per-call key without including it in cache identifiers."""
        embeddings = [[0.1, 0.2]]
        mock_api.return_value = _text_resp(embeddings, 12)
        cache = MagicMock()
        cache.retrieve = AsyncMock(return_value=None)
        cache.store = AsyncMock()
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="text-embedding-v4",
            dimensions=2,
            embedding_cache=cache,
        )

        await model(["hello"], api_key="sk-per-call")

        identifier = {
            "input": ["hello"],
            "model": "text-embedding-v4",
            "dimension": 2,
        }
        mock_api.assert_called_once_with(
            **identifier,
            api_key="sk-per-call",
        )
        cache.retrieve.assert_awaited_once_with(identifier=identifier)
        cache.store.assert_awaited_once_with(
            identifier=identifier,
            embeddings=embeddings,
        )

    @patch("dashscope.embeddings.TextEmbedding.call")
    async def test_sdk_thread(self, mock_api: Any) -> None:
        """The SDK executes on a worker instead of the event loop thread."""
        loop_thread = get_ident()
        on_loop_thread: list[bool] = []

        def record_thread(**_kwargs: Any) -> MagicMock:
            """Capture the thread that executes the SDK request."""
            on_loop_thread.append(get_ident() == loop_thread)
            return _text_resp([[1.0]])

        mock_api.side_effect = record_thread
        model = DashScopeEmbeddingModel(
            credential=_cred(),
            model="text-embedding-v4",
            dimensions=1,
        )
        await model(["hello"])
        self.assertListEqual(on_loop_thread, [False])

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
    """Test multimodal batching, SDK requests, and embedding caches."""

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

    def _assert_response(
        self,
        response: EmbeddingResponse,
        dimension: int,
        source: str,
    ) -> None:
        """Compare the complete response, matching only dynamic fields."""
        self.assertEqual(
            asdict(response),
            {
                "embeddings": [[0.25] * dimension],
                "id": AnyValue(),
                "created_at": AnyValue(),
                "type": "embedding",
                "usage": {
                    "tokens": 0 if source == "cache" else 1,
                    "time": 0 if source == "cache" else AnyValue(),
                    "type": "embedding",
                },
                "source": source,
            },
        )

    @patch("dashscope.MultiModalEmbedding.call")
    async def test_file_cache_survives_sdk_input_mutation(
        self,
        sdk: Any,
    ) -> None:
        """The store identifier matches the retrieve identifier even when
        the SDK rewrites the input in place (upload preprocessing)."""

        def mutating_call(**kwargs: Any) -> SimpleNamespace:
            if kwargs["input"] and isinstance(kwargs["input"][0], dict):
                kwargs["input"][0]["image"] = "oss://uploaded/img.png"
            return _multimodal_resp(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            model = _multimodal_model(
                2560,
                "qwen3-vl-embedding",
                cache=FileEmbeddingCache(cache_dir=directory),
            )
            sdk.side_effect = mutating_call
            first = await model([_img()])

            sdk.side_effect = None
            sdk.return_value = _multimodal_resp(input=[{"image": "x"}])
            second = await model([_img()])

        self.assertEqual(first.source, "api")
        self.assertEqual(second.source, "cache")

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_multimodal_resp)
    async def test_file_cache_separates_dimensions(self, sdk: Any) -> None:
        """Different contracts must not read each other's stored vectors."""
        name = "tongyi-embedding-vision-plus"
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(cache_dir=directory)
            small = _multimodal_model(512, name=name, cache=cache)
            large = _multimodal_model(1024, name=name, cache=cache)
            first = await small(["same input"])
            second = await large(["same input"])
            repeated = await small(["same input"])
            self.assertEqual(
                sdk.call_args_list,
                [
                    call(
                        input=[{"text": "same input"}],
                        model=name,
                        api_key="offline-placeholder",
                        dimension=dimension,
                    )
                    for dimension in (512, 1024)
                ],
            )
            self._assert_response(first, 512, "api")
            self._assert_response(second, 1024, "api")
            self._assert_response(repeated, 512, "cache")

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_multimodal_resp)
    async def test_explicit_dimension_override(self, sdk: Any) -> None:
        """An explicit override determines the request and cache identity."""
        name = "tongyi-embedding-vision-plus"
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(cache_dir=directory)
            first = await _multimodal_model(1024, name=name, cache=cache)(
                ["same input"],
                dimension=512,
                output_type="dense",
            )
            second = await _multimodal_model(512, name=name, cache=cache)(
                ["same input"],
                output_type="dense",
            )
            model = _multimodal_model(1024, name=name, cache=cache)
            reordered = await model(
                ["same input"],
                output_type="dense",
                dimension=512,
            )
            self.assertEqual(
                sdk.call_args_list,
                [
                    call(
                        input=[{"text": "same input"}],
                        model=name,
                        api_key="offline-placeholder",
                        dimension=512,
                        output_type="dense",
                    ),
                ],
            )
            self._assert_response(first, 512, "api")
            self._assert_response(second, 512, "cache")
            self._assert_response(reordered, 512, "cache")

    @patch("dashscope.MultiModalEmbedding.call")
    async def test_multimodal_one_peace_singular_key(self, sdk: Any) -> None:
        """multimodal-embedding-one-peace-v1's singular output.embedding
        is normalized instead of raising KeyError."""
        sdk.return_value = SimpleNamespace(
            status_code=200,
            output={"embedding": [0.1, 0.2, 0.3]},
            usage={"input_tokens": 1, "image_tokens": 0},
        )
        model = _multimodal_model(3, "multimodal-embedding-one-peace-v1")

        result = await model(["hello", "world"])

        self.assertEqual(result.embeddings, [[0.1, 0.2, 0.3]])
        self.assertEqual(result.source, "api")

    @patch("dashscope.MultiModalEmbedding.call", side_effect=_multimodal_resp)
    async def test_multimodal_dimensions(self, sdk: Any) -> None:
        """Qwen3 and other multimodal models forward dimensions."""
        for name in ("qwen3-vl-embedding", "tongyi-embedding-vision-plus"):
            with self.subTest(model=name):
                sdk.reset_mock()
                response = await _multimodal_model(512, name=name)(["hello"])
                self.assertEqual(
                    sdk.call_args_list,
                    [
                        call(
                            input=[{"text": "hello"}],
                            model=name,
                            api_key="offline-placeholder",
                            dimension=512,
                        ),
                    ],
                )
                self._assert_response(response, 512, "api")
