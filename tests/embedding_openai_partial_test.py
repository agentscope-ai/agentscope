# -*- coding: utf-8 -*-
"""A partial embedding response must fail loudly, not be cached.

The provider is free to answer with fewer items than were sent, or to label
them with an index outside the batch. Either way the result is unusable, and
storing it would make it permanent.
"""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.credential import OpenAICredential
from agentscope.embedding import FileEmbeddingCache, OpenAIEmbeddingModel


def _response(
    items: list[tuple[int, Any]],
    total_tokens: int = 4,
) -> MagicMock:
    """Build a mock ``embeddings.create`` response from (index, vector)."""
    resp = MagicMock()
    resp.data = []
    for index, embedding in items:
        item = MagicMock()
        item.index = index
        item.embedding = embedding
        resp.data.append(item)
    resp.usage = MagicMock(total_tokens=total_tokens)
    return resp


class OpenAIEmbeddingPartialResponseTest(IsolatedAsyncioTestCase):
    """Truncated or mis-indexed responses must raise before caching."""

    @staticmethod
    def _model(cache_dir: str) -> OpenAIEmbeddingModel:
        """Build a model wired to a real cache in *cache_dir*."""
        return OpenAIEmbeddingModel(
            credential=OpenAICredential(api_key="k"),
            model="text-embedding-3-small",
            dimensions=1,
            embedding_cache=FileEmbeddingCache(cache_dir=cache_dir),
        )

    @patch("openai.AsyncClient")
    async def test_short_response_raises(self, mock_client_cls: Any) -> None:
        """Fewer vectors than inputs is an error, not ``None`` padding."""
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=_response([(0, [0.1]), (1, [0.2])]),
        )
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            model = self._model(tmp)
            with self.assertRaises(ValueError):
                await model(["a", "b", "c"])

    @patch("openai.AsyncClient")
    async def test_out_of_range_index_raises(
        self,
        mock_client_cls: Any,
    ) -> None:
        """An index outside the batch must not leave an invisible hole."""
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=_response([(0, [0.1]), (7, [0.2])]),
        )
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            model = self._model(tmp)
            with self.assertRaises(ValueError):
                await model(["a", "b"])

    @patch("openai.AsyncClient")
    async def test_missing_vector_in_item_raises(
        self,
        mock_client_cls: Any,
    ) -> None:
        """An item carrying no vector at all is also a partial response."""
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=_response([(0, [0.1]), (1, None)]),
        )
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            model = self._model(tmp)
            with self.assertRaises(ValueError):
                await model(["a", "b"])

    @patch("openai.AsyncClient")
    async def test_nothing_is_cached_after_a_failure(
        self,
        mock_client_cls: Any,
    ) -> None:
        """A rejected response must not be readable back from the cache."""
        mock_client = MagicMock()
        create = AsyncMock(return_value=_response([(0, [0.1])]))
        mock_client.embeddings.create = create
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            model = self._model(tmp)
            for _ in range(2):
                with self.assertRaises(ValueError):
                    await model(["a", "b"])

            # Two API calls, not one call plus a poisoned cache hit.
            self.assertEqual(create.await_count, 2)

    @patch("openai.AsyncClient")
    async def test_complete_response_is_unaffected(
        self,
        mock_client_cls: Any,
    ) -> None:
        """The happy path still returns vectors and populates the cache."""
        mock_client = MagicMock()
        create = AsyncMock(
            return_value=_response([(0, [0.1]), (1, [0.2])]),
        )
        mock_client.embeddings.create = create
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            model = self._model(tmp)

            first = await model(["a", "b"])
            self.assertEqual(first.embeddings, [[0.1], [0.2]])
            self.assertEqual(first.source, "api")

            second = await model(["a", "b"])
            self.assertEqual(second.embeddings, [[0.1], [0.2]])
            self.assertEqual(second.source, "cache")
            self.assertEqual(create.await_count, 1)
