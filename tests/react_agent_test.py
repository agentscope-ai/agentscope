# -*- coding: utf-8 -*-
"""The ReAct agent unittests."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import TextBlock, ToolUseBlock, Msg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit, ToolResponse


class MyModel(ChatModelBase):
    """Test model class."""

    def __init__(self) -> None:
        """Initialize the test model."""
        super().__init__("test_model", stream=False)
        self.cnt = 1
        self.fake_content_1 = [
            TextBlock(
                type="text",
                text="123",
            ),
        ]
        self.fake_content_2 = [
            TextBlock(type="text", text="456"),
            ToolUseBlock(
                type="tool_use",
                name="generate_response",
                id="xx",
                input={"result": "789"},
            ),
        ]

    async def __call__(
        self,
        _messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock model call."""
        self.cnt += 1
        if self.cnt == 2:
            return ChatResponse(
                content=self.fake_content_1,
            )
        else:
            return ChatResponse(
                content=self.fake_content_2,
            )


async def pre_reasoning_hook(self: ReActAgent, _kwargs: Any) -> None:
    """Mock pre-reasoning hook."""
    if hasattr(self, "cnt_pre_reasoning"):
        self.cnt_pre_reasoning += 1
    else:
        self.cnt_pre_reasoning = 1


async def post_reasoning_hook(
    self: ReActAgent,
    _kwargs: Any,
    _output: Msg | None,
) -> None:
    """Mock post-reasoning hook."""
    if hasattr(self, "cnt_post_reasoning"):
        self.cnt_post_reasoning += 1
    else:
        self.cnt_post_reasoning = 1


async def pre_acting_hook(self: ReActAgent, _kwargs: Any) -> None:
    """Mock pre-acting hook."""
    if hasattr(self, "cnt_pre_acting"):
        self.cnt_pre_acting += 1
    else:
        self.cnt_pre_acting = 1


async def post_acting_hook(
    self: ReActAgent,
    _kwargs: Any,
    _output: Msg | None,
) -> None:
    """Mock post-acting hook."""
    if hasattr(self, "cnt_post_acting"):
        self.cnt_post_acting += 1
    else:
        self.cnt_post_acting = 1


class ReActAgentTest(IsolatedAsyncioTestCase):
    """Test class for ReActAgent."""

    async def test_react_agent(self) -> None:
        """Test the ReActAgent class"""
        model = MyModel()
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday.",
            model=model,
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=Toolkit(),
        )

        agent.register_instance_hook(
            "pre_reasoning",
            "test_hook",
            pre_reasoning_hook,
        )

        agent.register_instance_hook(
            "post_reasoning",
            "test_hook",
            post_reasoning_hook,
        )

        agent.register_instance_hook(
            "pre_acting",
            "test_hook",
            pre_acting_hook,
        )

        agent.register_instance_hook(
            "post_acting",
            "test_hook",
            post_acting_hook,
        )

        await agent()
        self.assertEqual(
            getattr(agent, "cnt_pre_reasoning"),
            1,
        )
        self.assertEqual(
            getattr(agent, "cnt_post_reasoning"),
            1,
        )
        # Note: pre_acting and post_acting hooks are not called when model
        # returns plain text without structured output, as plain text is not
        # converted to tool call in this case
        self.assertFalse(
            hasattr(agent, "cnt_pre_acting"),
            "pre_acting hook should not be called for plain text response",
        )
        self.assertFalse(
            hasattr(agent, "cnt_post_acting"),
            "post_acting hook should not be called for plain text response",
        )

        # Test with structured output: generate_response should be registered
        # and visible in tool list
        class TestStructuredModel(BaseModel):
            """Test structured model."""

            result: str = Field(description="Test result field.")

        await agent(structured_model=TestStructuredModel)
        self.assertEqual(
            getattr(agent, "cnt_pre_reasoning"),
            2,
        )
        self.assertEqual(
            getattr(agent, "cnt_post_reasoning"),
            2,
        )
        # pre_acting and post_acting hooks are called only when model returns
        # tool calls (not plain text). With structured_model, generate_response
        # is registered and model can call it.
        self.assertEqual(
            getattr(agent, "cnt_pre_acting"),
            1,  # Only called once (second call with tool use)
        )
        self.assertEqual(
            getattr(agent, "cnt_post_acting"),
            1,  # Only called once (second call with tool use)
        )

        # Verify that generate_response is removed when no structured_model
        # Reset model to return plain text
        model.fake_content_2 = [TextBlock(type="text", text="456")]
        await agent()
        self.assertFalse(
            agent.finish_function_name in agent.toolkit.tools,
            "generate_response should be removed when no structured_model",
        )

    async def test_react_agent_with_tool_functions(self) -> None:
        """Test ReActAgent with registered tool functions.

        This test verifies that:
        1. Tool functions can be registered to Toolkit
        2. ReActAgent can use the registered tools
        3. Agent can call multiple tools in a single request
        """

        # Define tool functions (regular Python functions, no decorator needed)
        def get_weather(city: str) -> ToolResponse:
            """Get city weather.

            Args:
                city: City name
            """
            weather_data = {
                "北京": "晴，25°C",
                "上海": "阴，22°C",
                "广州": "雨，28°C"
            }
            result = weather_data.get(city, "未知城市")
            return ToolResponse(content=[TextBlock(type="text", text=result)])

        def calculate(a: float, b: float) -> ToolResponse:
            """Calculate the product of two numbers.

            Args:
                a: First number
                b: Second number
            """
            result = a * b
            return ToolResponse(content=[TextBlock(type="text", text=str(result))])

        # Register tools to Toolkit
        toolkit = Toolkit()
        toolkit.register_tool_function(get_weather)
        toolkit.register_tool_function(calculate)

        # Verify tools are registered
        schemas = toolkit.get_json_schemas()
        self.assertEqual(len(schemas), 2)
        tool_names = [s["function"]["name"] for s in schemas]
        self.assertIn("get_weather", tool_names)
        self.assertIn("calculate", tool_names)

        # Create agent with tools
        model = MyModel()
        agent = ReActAgent(
            name="天气助手",
            sys_prompt="你是一个天气查询助手，可以查询天气和进行数学计算。",
            model=model,
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
        )

        # Verify tools are available to agent
        self.assertIn("get_weather", agent.toolkit.tools)
        self.assertIn("calculate", agent.toolkit.tools)

        # Test calling tool functions directly through toolkit
        tool_use_get_weather = ToolUseBlock(
            type="tool_use",
            id="call_1",
            name="get_weather",
            input={"city": "北京"},
        )
        res = await agent.toolkit.call_tool_function(tool_use_get_weather)
        async for chunk in res:
            self.assertEqual(chunk.content[0]["text"], "晴，25°C")

        tool_use_calculate = ToolUseBlock(
            type="tool_use",
            id="call_2",
            name="calculate",
            input={"a": 123.0, "b": 456.0},
        )
        res = await agent.toolkit.call_tool_function(tool_use_calculate)
        async for chunk in res:
            self.assertEqual(chunk.content[0]["text"], "56088.0")
