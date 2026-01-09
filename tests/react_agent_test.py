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
from agentscope.tool import Toolkit


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
