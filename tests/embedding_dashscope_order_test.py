# -*- coding: utf-8 -*-
"""Keep indexed DashScope embedding responses aligned with their inputs."""
import tempfile
from importlib.util import find_spec
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch

from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel, FileEmbeddingCache
from agentscope.message import DataBlock, TextBlock, URLSource


class DashScopeEmbeddingOrderTest(IsolatedAsyncioTestCase):
    """Exercise response ordering through the public embedding interface."""

    async def _check_cached_order(self, multimodal: bool) -> None:
        """Indexed vectors must be reordered before both return and cache."""
        sdk_path, model_name, index_key = self._mode(multimodal)
        response = SimpleNamespace(
            status_code=200,
            output={
                "embeddings": [
                    {index_key: 1, "embedding": [0.0, 1.0]},
                    {index_key: 0, "embedding": [1.0, 0.0]},
                ],
            },
            usage={"total_tokens": 2, "input_tokens": 2},
        )
        inputs: list[Any] = ["first", "second"]
        if multimodal:
            inputs[1] = DataBlock(
                source=URLSource(
                    url="https://example.com/image.png",
                    media_type="image/png",
                ),
            )
        with tempfile.TemporaryDirectory() as cache_dir:
            model = DashScopeEmbeddingModel(
                credential=DashScopeCredential(api_key="offline-test"),
                model=model_name,
                dimensions=2,
                embedding_cache=FileEmbeddingCache(cache_dir=cache_dir),
            )
            with patch(sdk_path, return_value=response) as mock_api:
                first = await model(inputs)
                second = await model(inputs)

            self.assertEqual(first.embeddings, [[1.0, 0.0], [0.0, 1.0]])
            self.assertEqual(second.embeddings, first.embeddings)
            self.assertEqual(second.source, "cache")
            self.assertEqual(mock_api.call_count, 1)

    @staticmethod
    def _mode(multimodal: bool) -> tuple[str, str, str]:
        """Select each SDK's model and documented input-index field."""
        if multimodal:
            return (
                "dashscope.MultiModalEmbedding.call",
                "qwen3-vl-embedding",
                "index",
            )
        return (
            "dashscope.embeddings.TextEmbedding.call",
            "text-embedding-v4",
            "text_index",
        )

    async def test_text_indices_are_restored_before_caching(self) -> None:
        """Text response indices identify the original text inputs."""
        await self._check_cached_order(multimodal=False)

    async def test_multimodal_indices_are_restored_before_caching(
        self,
    ) -> None:
        """Multimodal response indices identify text and image inputs."""
        await self._check_cached_order(multimodal=True)

    async def test_multiple_batches_preserve_input_order(self) -> None:
        """Indices are local to each SDK batch, including a final singleton."""
        for multimodal in (False, True):
            with self.subTest(multimodal=multimodal):
                sdk_path, model_name, index_key = self._mode(multimodal)

                def respond(
                    *,
                    current_multimodal: bool = multimodal,
                    current_index_key: str = index_key,
                    **kwargs: Any,
                ) -> SimpleNamespace:
                    """Return each batch in reverse index order."""
                    inputs = kwargs["input"]
                    values = [
                        float(item["text"] if current_multimodal else item)
                        for item in inputs
                    ]
                    return SimpleNamespace(
                        status_code=200,
                        output={
                            "embeddings": [
                                {
                                    current_index_key: i,
                                    "embedding": [values[i], 1.0],
                                }
                                for i in reversed(range(len(values)))
                            ],
                        },
                        usage={"total_tokens": 2, "input_tokens": 2},
                    )

                model = DashScopeEmbeddingModel(
                    credential=DashScopeCredential(api_key="offline-test"),
                    model=model_name,
                    dimensions=2,
                )
                with patch(sdk_path, side_effect=respond) as mock_api:
                    result = await model([str(i) for i in range(21)])

                self.assertEqual(
                    result.embeddings,
                    [[float(i), 1.0] for i in range(21)],
                )
                self.assertEqual(mock_api.call_count, 2 if multimodal else 3)

    async def test_unindexed_responses_preserve_sdk_order(self) -> None:
        """Keep compatibility with responses without explicit indices."""
        for multimodal in (False, True):
            with self.subTest(multimodal=multimodal):
                sdk_path, model_name, _ = self._mode(multimodal)
                vectors = [[1.0, 0.0], [0.0, 1.0]]
                response = SimpleNamespace(
                    status_code=200,
                    output={
                        "embeddings": [{"embedding": e} for e in vectors],
                    },
                    usage={"total_tokens": 2, "input_tokens": 2},
                )
                model = DashScopeEmbeddingModel(
                    credential=DashScopeCredential(api_key="offline-test"),
                    model=model_name,
                    dimensions=2,
                )
                with patch(sdk_path, return_value=response):
                    result = await model(["first", "second"])
                self.assertEqual(result.embeddings, vectors)

    @skipUnless(find_spec("qdrant_client"), "qdrant-client is not installed")
    async def test_indexed_vectors_retrieve_corresponding_chunk(self) -> None:
        """Index each chunk with its own vector after a reordered response."""
        from agentscope.rag import Chunk, KnowledgeBase, QdrantStore

        for multimodal in (False, True):
            with self.subTest(multimodal=multimodal):
                sdk_path, model_name, index_key = self._mode(multimodal)
                response = SimpleNamespace(
                    status_code=200,
                    output={
                        "embeddings": [
                            {index_key: 1, "embedding": [0.0, 1.0]},
                            {index_key: 0, "embedding": [1.0, 0.0]},
                        ],
                    },
                    usage={"total_tokens": 2, "input_tokens": 2},
                )
                model = DashScopeEmbeddingModel(
                    credential=DashScopeCredential(api_key="offline-test"),
                    model=model_name,
                    dimensions=2,
                )
                async with QdrantStore(location=":memory:") as store:
                    knowledge = KnowledgeBase(
                        name="documents",
                        description="Offline test documents",
                        embedding_model=model,
                        vector_store=store,
                        collection="documents",
                    )
                    chunks = [
                        Chunk(
                            content=TextBlock(text=text),
                            source="document.txt",
                            chunk_index=i,
                            total_chunks=2,
                        )
                        for i, text in enumerate(("first", "second"))
                    ]
                    with patch(sdk_path, return_value=response):
                        await knowledge.insert_document(chunks, "doc")
                    hits = await store.search(
                        "documents",
                        query_vector=[1.0, 0.0],
                        top_k=1,
                    )
                    self.assertEqual(len(hits), 1)
                    self.assertEqual(hits[0].chunk.chunk_index, 0)
                    self.assertIsInstance(hits[0].chunk.content, TextBlock)
                    self.assertEqual(hits[0].chunk.content.text, "first")
