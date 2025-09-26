# -*- coding: utf-8 -*-
"""Unittests for anthropic model."""
import unittest
from unittest.mock import patch, MagicMock

import agentscope
from agentscope.manager import ModelManager
from agentscope.model import ModelResponse


class AnthropicModelWrapperTest(unittest.TestCase):
    """Anthropic model wrapper unittests."""

    def setUp(self) -> None:
        """Init for ExampleTest."""
        agentscope.init(
            model_configs={
                "config_name": "claude-3-5",
                "model_type": "anthropic_chat",
                "model_name": "claude-3-5-sonnet-20241022",
                "api_key": "xxx",
                "stream": False,
            },
            save_api_invoke=False,
        )
        self.mock_response = {
            "id": "msg_018zRB2VgEx2hS5TGxMhLz6Y",
            "content": [
                {
                    "text": "你好！我是 Bob。今天天气不错，你觉得呢?",
                    "type": "text",
                },
            ],
            "model": "claude-3-5-sonnet-20241022",
            "role": "assistant",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "type": "message",
            "usage": {
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "input_tokens": 21,
                "output_tokens": 26,
            },
        }

        self.model_response_gt = ModelResponse(
            text="你好！我是 Bob。今天天气不错，你觉得呢?",
            raw=self.mock_response,
        )

    @patch("anthropic.Anthropic")
    def test_model_calling(self, mock_anthropic: MagicMock) -> None:
        """Test if the model is called successfully."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "id": "msg_018zRB2VgEx2hS5TGxMhLz6Y",
            "content": [
                {
                    "text": "你好！我是 Bob。今天天气不错，你觉得呢?",
                    "type": "text",
                },
            ],
            "model": "claude-3-5-sonnet-20241022",
            "role": "assistant",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "type": "message",
            "usage": {
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "input_tokens": 21,
                "output_tokens": 26,
            },
        }

        mock_client = mock_anthropic.return_value

        mock_client.messages.create.return_value = mock_response

        model = ModelManager.get_instance().get_model_by_config_name(
            "claude-3-5",
        )
        response = model([{"role": "user", "content": "你好"}])

        self.assertEqual(response.raw, self.model_response_gt.raw)
        self.assertEqual(response.text, self.model_response_gt.text)


if __name__ == "__main__":
    unittest.main()
