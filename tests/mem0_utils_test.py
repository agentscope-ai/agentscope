# -*- coding: utf-8 -*-
"""Unit tests for AgentScopeLLM with Ollama using asyncio.gather() for
parallel calls."""
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock, MagicMock

from agentscope.memory._long_term_memory._mem0._mem0_utils import AgentScopeLLM
from agentscope.model import OllamaChatModel

# Try to import BaseLlmConfig, but handle ImportError gracefully
try:
    from mem0.configs.llms.base import BaseLlmConfig
except ImportError:
    # If mem0 is not installed, create a mock class
    BaseLlmConfig = MagicMock


class OllamaMessageMock:
    """Mock class for Ollama message objects."""

    def __init__(
        self,
        content: str = "",
        thinking: str = "",
        tool_calls: list = None,
    ) -> None:
        self.content = content
        self.thinking = thinking
        self.tool_calls = tool_calls or []


class OllamaResponseMock:
    """Mock class for Ollama response objects."""

    def __init__(
        self,
        content: str = "",
        thinking: str = "",
        tool_calls: list = None,
        prompt_eval_count: int = 10,
        eval_count: int = 20,
    ) -> None:
        self.message = OllamaMessageMock(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls or [],
        )
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count

    def get(self, key: str, default: Any | None = None) -> Any:
        """Mock dict-like get method."""
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        """Mock dict-like contains method to support 'in' operator."""
        return hasattr(self, key)


class TestAgentScopeLLMWithOllama(IsolatedAsyncioTestCase):
    """Test cases for AgentScopeLLM with
    OllamaChatModel using asyncio.gather()."""

    def test_agentscope_llm_parallel_calls_with_asyncio_gather(self) -> None:
        """Test parallel calls using asyncio.gather() - original bug scenario.

        This test reproduces the original bug where parallel calls using
        asyncio.gather() would cause "Event loop is closed" errors.
        The persistent event loop management fix should resolve this issue.
        """
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Create OllamaChatModel instance
            ollama_model = OllamaChatModel(
                model_name="qwen3:14b",
                stream=False,
                enable_thinking=False,
            )
            ollama_model.client = mock_client

            # Mock Ollama chat response
            mock_ollama_response = OllamaResponseMock(
                content="Test response",
            )
            mock_client.chat = AsyncMock(
                return_value=mock_ollama_response,
            )

            # Create AgentScopeLLM config
            # Directly set model attribute, simpler than using
            # LlmConfig constructor
            llm_config = BaseLlmConfig()
            llm_config.model = ollama_model

            llm = AgentScopeLLM(config=llm_config)

            # Create multiple different messages for parallel calls
            messages_list = [
                [{"role": "user", "content": "I like staying in homestays"}],
                [{"role": "user", "content": "I prefer coffee over tea"}],
                [{"role": "user", "content": "My favorite color is blue"}],
                [{"role": "user", "content": "I work as a software engineer"}],
                [
                    {
                        "role": "user",
                        "content": "I enjoy reading science fiction",
                    },
                ],
            ]

            # Define async function to call generate_response
            async def call_llm(messages: list[dict[str, str]]) -> str | dict:
                """Call LLM generate_response in async context."""
                return llm.generate_response(messages)

            # Use asyncio.gather() to make parallel calls
            # Without the fix, this would fail with
            # "Event loop is closed" error
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                results = loop.run_until_complete(
                    asyncio.gather(
                        *[call_llm(msgs) for msgs in messages_list],
                    ),
                )
            finally:
                loop.close()

            # Verify all parallel calls completed successfully
            self.assertEqual(len(results), len(messages_list))
            for result in results:
                self.assertIsInstance(result, str)
                self.assertGreater(len(result), 0)

            # Verify Ollama client was called for each parallel request
            self.assertEqual(
                mock_client.chat.call_count,
                len(messages_list),
            )

    async def test_agentscope_llm_async_gather_in_async_context(self) -> None:
        """Test asyncio.gather() in an async test context.

        This test uses the async test framework to properly test
        parallel calls using asyncio.gather().
        """
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Create OllamaChatModel instance
            ollama_model = OllamaChatModel(
                model_name="qwen3:14b",
                stream=False,
                enable_thinking=False,
            )
            ollama_model.client = mock_client

            # Mock Ollama chat response
            mock_ollama_response = OllamaResponseMock(
                content="Test response",
            )
            mock_client.chat = AsyncMock(
                return_value=mock_ollama_response,
            )

            # Create AgentScopeLLM config
            # Directly set model attribute, simpler than using
            # LlmConfig constructor
            llm_config = BaseLlmConfig()
            llm_config.model = ollama_model

            llm = AgentScopeLLM(config=llm_config)

            messages_list = [
                [{"role": "user", "content": "First message"}],
                [{"role": "user", "content": "Second message"}],
                [{"role": "user", "content": "Third message"}],
            ]

            # Define async function to call generate_response
            async def call_llm(messages: list[dict[str, str]]) -> str | dict:
                """Call LLM generate_response."""
                return llm.generate_response(messages)

            # Use asyncio.gather() to make parallel calls
            # This is the exact scenario that was causing the bug
            results = await asyncio.gather(
                *[call_llm(msgs) for msgs in messages_list],
            )

            # Verify all parallel calls completed successfully
            self.assertEqual(len(results), len(messages_list))
            for result in results:
                self.assertIsInstance(result, str)
                self.assertGreater(len(result), 0)

            # Verify Ollama client was called for each parallel request
            self.assertEqual(
                mock_client.chat.call_count,
                len(messages_list),
            )
