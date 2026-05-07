# -*- coding: utf-8 -*-
"""Unit tests for DeepSeek API (Anthropic-compatible) model class."""

import os
from unittest.async_case import IsolatedAsyncioTestCase

# Remove environment variable that interferes with API key
if "ANTHROPIC_AUTH_TOKEN" in os.environ:
    del os.environ["ANTHROPIC_AUTH_TOKEN"]

import agentscope
from agentscope.agent import ReActAgent
from agentscope.formatter import AnthropicChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.model import AnthropicChatModel, ChatResponse
from agentscope.pipeline import FanoutPipeline, SequentialPipeline
from agentscope.tool import Toolkit
from agentscope.tool._coding import execute_python_code
from agentscope.tool._response import ToolResponse

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-a792be7e2f0e4fe586fb1ceac312cf29")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
DEEPSEEK_MODEL_NAME = os.environ.get("DEEPSEEK_MODEL_NAME", "deepseek-v4-pro")


class TestMiniMaxModel(IsolatedAsyncioTestCase):
    """Test cases for DeepSeek API (Anthropic-compatible) model."""

    @staticmethod
    def _create_model(stream: bool = False, **kwargs):
        """Create a DeepSeek model instance with common configuration."""
        return AnthropicChatModel(
            model_name=DEEPSEEK_MODEL_NAME,
            api_key=DEEPSEEK_API_KEY,
            stream=stream,
            client_kwargs={"base_url": DEEPSEEK_BASE_URL},
            **kwargs,
        )

    @staticmethod
    def _create_agent(name, sys_prompt, model=None, toolkit=None, **kwargs):
        """Create a ReActAgent with common configuration."""
        return ReActAgent(
            name=name,
            sys_prompt=sys_prompt,
            model=model or TestMiniMaxModel._create_model(),
            formatter=AnthropicChatFormatter(),
            toolkit=toolkit,
            **kwargs,
        )

    def test_init_with_custom_base_url(self) -> None:
        """Test initialization with custom base_url for DeepSeek API."""
        model = self._create_model()
        self.assertEqual(model.model_name, DEEPSEEK_MODEL_NAME)

    async def test_call_with_deepseek_api(self) -> None:
        """Test calling DeepSeek API with a simple prompt."""
        model = self._create_model()
        messages = [{"role": "user", "content": "Hello, say 'hi' in one word"}]
        result = await model(messages)
        self.assertIsInstance(result, ChatResponse)
        self.assertTrue(len(result.content) > 0)
        print(f"Response: {result.content}")

    async def test_react_agent_with_deepseek(self) -> None:
        """Test ReActAgent with DeepSeek API."""
        toolkit = Toolkit()
        toolkit.register_tool_function(execute_python_code)

        agent = self._create_agent(
            name="助手",
            sys_prompt="你是一个有帮助的编程助手。",
            toolkit=toolkit,
        )

        msg = Msg(name="user", content="请用 Java 写一个快速排序算法", role="user")
        response = await agent(msg)
        self.assertIsNotNone(response)
        print(f"Agent Response: {response}")

    async def test_react_agent_with_custom_tools(self) -> None:
        """Test ReActAgent with custom tools (get_weather and calculate)."""
        agentscope.init(project="tool-agent")

        def get_weather(city: str) -> ToolResponse:
            """获取城市天气

            Args:
                city: 城市名称
            """
            weather_data = {
                "北京": "晴，25°C",
                "上海": "阴，22°C",
                "广州": "雨，28°C"
            }
            result = weather_data.get(city, "未知城市")
            return ToolResponse(content=[TextBlock(type="text", text=result)])

        def calculate(a: float, b: float) -> ToolResponse:
            """计算两个数的乘积

            Args:
                a: 第一个数
                b: 第二个数
            """
            result = a * b
            return ToolResponse(content=[TextBlock(type="text", text=str(result))])

        model = self._create_model(thinking={"type": "disabled"})

        toolkit = Toolkit()
        toolkit.register_tool_function(get_weather)
        toolkit.register_tool_function(calculate)

        agent = self._create_agent(
            name="天气助手",
            sys_prompt="你是一个天气查询助手，可以查询天气和进行数学计算。",
            model=model,
            toolkit=toolkit,
        )

        # DeepSeek Anthropic endpoint 对并行工具调用有 message 顺序限制，
        # 因此分两次请求来规避问题
        msg1 = Msg(name="user", content="北京今天天气怎么样？", role="user")
        response = await agent(msg1)
        self.assertIsNotNone(response)
        print(f"Weather Agent Response: {response}")

        msg2 = Msg(name="user", content="帮我算一下 123 乘以 456", role="user")
        response = await agent(msg2)
        self.assertIsNotNone(response)
        print(f"Calculate Agent Response: {response}")

    async def test_sequential_pipeline(self) -> None:
        """Test SequentialPipeline - sequential execution of agents."""
        agentscope.init(project="sequential-pipeline")

        researcher, writer = self._create_researcher_writer_pair()
        seq = SequentialPipeline(agents=[researcher, writer])
        seq_result = await seq(Msg(name="user", content="AI 是什么？", role="user"))
        self.assertIsNotNone(seq_result)
        print(f"SequentialPipeline Result: {seq_result}")

    async def test_fanout_pipeline(self) -> None:
        """Test FanoutPipeline - parallel execution of agents."""
        agentscope.init(project="fanout-pipeline")

        researcher, writer = self._create_researcher_writer_pair()
        fanout = FanoutPipeline(agents=[researcher, writer], enable_gather=True)
        fanout_results = await fanout(Msg(name="user", content="解释一下什么是 AI Agent", role="user"))
        self.assertIsInstance(fanout_results, list)
        self.assertEqual(len(fanout_results), 2)
        print(f"FanoutPipeline Results: {fanout_results}")

    def _create_researcher_writer_pair(self):
        """Create a (researcher, writer) agent pair for pipeline tests."""
        researcher = self._create_agent(
            name="研究员",
            sys_prompt="你是一个研究助手，负责收集和分析信息。用简洁的语言回答。",
        )
        writer = self._create_agent(
            name="作家",
            sys_prompt="你是一个技术写作助手，负责将研究结果整理成简洁的文章。用中文回答。",
        )
        return researcher, writer


if __name__ == "__main__":
    import unittest
    unittest.main()
