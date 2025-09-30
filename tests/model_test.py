# -*- coding: utf-8 -*-
"""Unit tests for model wrapper classes and functions"""
from typing import Any, Union, List, Sequence
import unittest
from unittest.mock import patch, MagicMock

import agentscope
from agentscope.manager import ModelManager, ASManager
from agentscope.message import Msg
from agentscope.model import (
    ModelResponse,
    ModelWrapperBase,
    YiChatWrapper,
    LiteLLMChatWrapper,
    ZhipuAIEmbeddingWrapper,
    ZhipuAIChatWrapper,
    GeminiEmbeddingWrapper,
    GeminiChatWrapper,
    OllamaGenerationWrapper,
    OllamaEmbeddingWrapper,
    OllamaChatWrapper,
    DashScopeMultiModalWrapper,
    DashScopeTextEmbeddingWrapper,
    DashScopeChatWrapper,
    DashScopeImageSynthesisWrapper,
    OpenAIEmbeddingWrapper,
    OpenAIDALLEWrapper,
    OpenAIChatWrapper,
    PostAPIChatWrapper,
    AnthropicChatWrapper,
)


class TestModelWrapperSimple(ModelWrapperBase):
    """A simple model wrapper class for test usage"""

    model_type: str = "TestModelWrapperSimple"

    def __call__(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return ModelResponse(text=self.config_name)

    def format(
        self,
        *args: Union[Msg, Sequence[Msg], None],
    ) -> Union[List[dict], str]:
        """Format the input for the model"""
        print(*args)
        return ""


class BasicModelTest(unittest.TestCase):
    """Test cases for basic model wrappers"""

    def setUp(self) -> None:
        """Init for BasicModelTest"""
        agentscope.init(disable_saving=True)

    def test_build_in_model_wrapper_classes(self) -> None:
        """Test the build in model wrapper classes."""
        # get model wrapper class by class name
        self.assertDictEqual(
            ModelManager.get_instance().model_wrapper_mapping,
            {
                "post_api_chat": PostAPIChatWrapper,
                "openai_chat": OpenAIChatWrapper,
                "openai_dall_e": OpenAIDALLEWrapper,
                "openai_embedding": OpenAIEmbeddingWrapper,
                "dashscope_chat": DashScopeChatWrapper,
                "dashscope_image_synthesis": DashScopeImageSynthesisWrapper,
                "dashscope_text_embedding": DashScopeTextEmbeddingWrapper,
                "dashscope_multimodal": DashScopeMultiModalWrapper,
                "ollama_chat": OllamaChatWrapper,
                "ollama_embedding": OllamaEmbeddingWrapper,
                "ollama_generate": OllamaGenerationWrapper,
                "gemini_chat": GeminiChatWrapper,
                "gemini_embedding": GeminiEmbeddingWrapper,
                "zhipuai_chat": ZhipuAIChatWrapper,
                "zhipuai_embedding": ZhipuAIEmbeddingWrapper,
                "litellm_chat": LiteLLMChatWrapper,
                "yi_chat": YiChatWrapper,
                "anthropic_chat": AnthropicChatWrapper,
            },
        )

    @patch("loguru.logger.warning")
    def test_load_model_configs(self, mock_logging: MagicMock) -> None:
        """Test to load model configs"""
        configs = [
            {
                "model_type": "openai_chat",
                "config_name": "gpt-4",
                "model_name": "gpt-4",
                "api_key": "xxx",
                "organization": "xxx",
                "generate_args": {"temperature": 0.5},
            },
            {
                "model_type": "post_api_chat",
                "config_name": "my_post_api",
                "model_name": "llama",
                "api_url": "https://xxx",
                "headers": {},
                "json_args": {},
            },
        ]
        # load a list of configs
        model_manager = ModelManager.get_instance()
        model_manager.load_model_configs(
            model_configs=configs,
            clear_existing=True,
        )

        model = model_manager.get_model_by_config_name("gpt-4")
        self.assertEqual(model.config_name, "gpt-4")
        model = model_manager.get_model_by_config_name("my_post_api")
        self.assertEqual(model.config_name, "my_post_api")
        self.assertRaises(
            ValueError,
            model_manager.get_model_by_config_name,
            "non_existent_id",
        )

        # load a single config
        model_manager.load_model_configs(
            model_configs=configs[0],
            clear_existing=True,
        )
        model = model_manager.get_model_by_config_name("gpt-4")
        self.assertEqual(model.config_name, "gpt-4")
        self.assertRaises(
            ValueError,
            model_manager.get_model_by_config_name,
            "my_post_api",
        )

        # load model with the same id
        model_manager.load_model_configs(
            model_configs=configs[0],
            clear_existing=False,
        )
        mock_logging.assert_called_once_with(
            "Config name [gpt-4] already exists.",
        )

    def test_register_model_wrapper_class(self) -> None:
        """Test the model wrapper class registration."""
        model_manager = ModelManager.get_instance()
        model_manager.load_model_configs(
            model_configs={
                "model_type": "TestModelWrapperSimple",
                "model_name": "test_model_wrapper",
                "config_name": "test_model_wrapper",
                "args": {},
            },
        )

        # Not registered model wrapper class
        self.assertRaises(
            ValueError,
            model_manager.get_model_by_config_name,
            "test_model_wrapper",
        )

        # Register model wrapper class
        agentscope.register_model_wrapper_class(TestModelWrapperSimple)

        test_model = model_manager.get_model_by_config_name(
            "test_model_wrapper",
        )

        response = test_model()
        self.assertEqual(response.text, "test_model_wrapper")
        model_manager.clear_model_configs()
        self.assertRaises(
            ValueError,
            model_manager.get_model_by_config_name,
            "test_model_wrapper",
        )

    def tearDown(self) -> None:
        """Clean up the test environment"""
        ASManager.get_instance().flush()
