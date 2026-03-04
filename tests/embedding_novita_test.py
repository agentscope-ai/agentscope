# -*- coding: utf-8 -*-
"""Unit tests for Novita AI embedding model class."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock

from agentscope.embedding import NovitaTextEmbedding, EmbeddingResponse


class TestNovitaTextEmbedding(IsolatedAsyncioTestCase):
    """Test cases for NovitaTextEmbedding."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("openai.AsyncClient") as mock_client:
            model = NovitaTextEmbedding(model_name="BAAI/bge-m3", api_key="test_key")
            self.assertEqual(model.model_name, "BAAI/bge-m3")
            mock_client.assert_called_once()
            call_args = mock_client.call_args[1]
            self.assertEqual(call_args["api_key"], "test_key")
            self.assertEqual(call_args["base_url"], "https://api.novita.ai/openai")

    async def test_call_novita_embedding(self) -> None:
        """Test calling Novita embedding model."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = NovitaTextEmbedding(
                model_name="BAAI/bge-m3",
                api_key="test_key"
            )
            model.client = mock_client

            texts = ["Hello world"]
            
            # Mock response
            mock_data = AsyncMock()
            mock_data.embedding = [0.1, 0.2, 0.3]
            
            mock_response = AsyncMock()
            mock_response.data = [mock_data]
            mock_response.usage = AsyncMock()
            mock_response.usage.total_tokens = 2

            mock_client.embeddings.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(texts)
            self.assertIsInstance(result, EmbeddingResponse)
            self.assertEqual(result.embeddings, [[0.1, 0.2, 0.3]])
            self.assertEqual(result.usage.tokens, 2)
