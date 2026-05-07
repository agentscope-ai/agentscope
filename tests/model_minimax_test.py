# -*- coding: utf-8 -*-
"""Unit tests for MiniMax API (Anthropic-compatible) model class."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.model import AnthropicChatModel, ChatResponse
from agentscope.agent import ReActAgent
from agentscope.formatter import AnthropicChatFormatter
from agentscope.tool import Toolkit
from agentscope.tool._coding import execute_python_code
from agentscope.message import Msg

MINIMAX_API_KEY = "sk-cp-Spitxq5Tkho8wE-td5ostrWUt-WywEARyjoQBjO1wD0AbSCrVP2-h9d5GgSQYzY2KcfuAgvkdh7rIYj8XmndZSAoFvA6a6vet3C0jHaVD5s7jDl7ckbwsCE"
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
MINIMAX_MODEL_NAME = "MiniMax-M2.7-highspeed"


class TestMiniMaxModel(IsolatedAsyncioTestCase):
    """Test cases for MiniMax API (Anthropic-compatible) model."""

    def test_init_with_custom_base_url(self) -> None:
        """Test initialization with custom base_url for MiniMax API."""
        model = AnthropicChatModel(
            model_name=MINIMAX_MODEL_NAME,
            api_key=MINIMAX_API_KEY,
            client_kwargs={
                "base_url": MINIMAX_BASE_URL,
            },
        )
        self.assertEqual(model.model_name, MINIMAX_MODEL_NAME)

    async def test_call_with_minimax_api(self) -> None:
        """Test calling MiniMax API with a simple prompt."""
        model = AnthropicChatModel(
            model_name=MINIMAX_MODEL_NAME,
            api_key=MINIMAX_API_KEY,
            stream=False,
            client_kwargs={
                "base_url": MINIMAX_BASE_URL,
            },
        )

        messages = [{"role": "user", "content": "Hello, say 'hi' in one word"}]
        result = await model(messages)

        self.assertIsInstance(result, ChatResponse)
        self.assertTrue(len(result.content) > 0)
        print(f"Response: {result.content}")

    async def test_react_agent_with_minimax(self) -> None:
        """Test ReActAgent with MiniMax API."""
        model = AnthropicChatModel(
            model_name=MINIMAX_MODEL_NAME,
            api_key=MINIMAX_API_KEY,
            stream=False,
            client_kwargs={
                "base_url": MINIMAX_BASE_URL,
            },
        )

        toolkit = Toolkit()
        toolkit.register_tool_function(execute_python_code)

        agent = ReActAgent(
            name="助手",
            sys_prompt="你是一个有帮助的编程助手。",
            model=model,
            formatter=AnthropicChatFormatter(),
            toolkit=toolkit,
        )

        msg = Msg(name="user", content="请用 Java 写一个快速排序算法", role="user")
        response = await agent(msg)
        self.assertIsNotNone(response)
        print(f"Agent Response: {response}")


if __name__ == "__main__":
    import unittest
    unittest.main()