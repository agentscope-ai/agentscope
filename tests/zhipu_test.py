# -*- coding: utf-8 -*-
"""zhipuai test"""
import unittest
from unittest.mock import patch, MagicMock

import agentscope
from agentscope.manager import ModelManager, ASManager


class TestZhipuAIChatWrapper(unittest.TestCase):
    """Test ZhipuAI Chat Wrapper"""

    def setUp(self) -> None:
        self.api_key = "test_api_key.secret_key"
        self.messages = [
            {"role": "user", "content": "Hello, ZhipuAI!"},
            {"role": "assistant", "content": "How can I assist you?"},
        ]

    @patch("agentscope.model.zhipu_model.zhipuai")
    def test_chat(self, mock_zhipuai: MagicMock) -> None:
        """
        Test chat"""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "choices": [
                {"message": {"content": "Hello, this is a mocked response!"}},
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
            },
        }
        mock_response.choices[
            0
        ].message.content = "Hello, this is a mocked response!"
        mock_zhipuai_client = MagicMock()
        mock_zhipuai.ZhipuAI.return_value = mock_zhipuai_client

        mock_zhipuai_client.chat.completions.create.return_value = (
            mock_response
        )

        agentscope.init(
            model_configs={
                "config_name": "test_config",
                "model_type": "zhipuai_chat",
                "model_name": "glm-4",
                "api_key": self.api_key,
            },
            disable_saving=True,
        )
        model = ModelManager.get_instance().get_model_by_config_name(
            "test_config",
        )

        response = model(messages=self.messages)

        self.assertEqual(response.text, "Hello, this is a mocked response!")

        mock_zhipuai_client.chat.completions.create.assert_called_once()

    def tearDown(self) -> None:
        """Tear down the test"""
        ASManager.get_instance().flush()


class TestZhipuAIEmbeddingWrapper(unittest.TestCase):
    """Test ZhipuAI Embedding Wrapper"""

    def setUp(self) -> None:
        self.api_key = "test_api_key"
        self.model_name = "embedding-2"
        self.text_to_embed = "This is a test sentence for embedding."

    @patch("agentscope.model.zhipu_model.zhipuai")
    def test_embedding(self, mock_zhipuai: MagicMock) -> None:
        """Test embedding API"""
        mock_embedding_response = MagicMock()
        mock_embedding_response.model_dump.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
        }

        mock_zhipuai_client = MagicMock()
        mock_zhipuai.ZhipuAI.return_value = mock_zhipuai_client
        mock_zhipuai_client.embeddings.create.return_value = (
            mock_embedding_response
        )

        agentscope.init(
            model_configs={
                "config_name": "test_embedding",
                "model_type": "zhipuai_embedding",
                "model_name": self.model_name,
                "api_key": self.api_key,
            },
            disable_saving=True,
        )

        model = ModelManager.get_instance().get_model_by_config_name(
            "test_embedding",
        )

        response = model(self.text_to_embed)

        expected_embedding = [[0.1, 0.2, 0.3]]
        self.assertEqual(response.embedding, expected_embedding)

        mock_zhipuai_client.embeddings.create.assert_called_once_with(
            input=self.text_to_embed,
            model=self.model_name,
            **{},
        )

    def tearDown(self) -> None:
        """Tear down the test"""
        ASManager.get_instance().flush()


if __name__ == "__main__":
    unittest.main()
