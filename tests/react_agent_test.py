# -*- coding: utf-8 -*-
"""The ReAct agent unittests."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

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
        self.fake_content = [TextBlock(type="text", text="123")]

    async def __call__(
        self,
        _messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock model call."""
        return ChatResponse(
            content=self.fake_content,
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
        self.assertEqual(
            getattr(agent, "cnt_pre_acting"),
            1,
        )
        self.assertEqual(
            getattr(agent, "cnt_post_acting"),
            1,
        )

        model.fake_content = [
            ToolUseBlock(
                type="tool_use",
                name=agent.finish_function_name,
                id="xx",
                input={"response": "123"},
            ),
        ]

        await agent()
        self.assertEqual(
            getattr(agent, "cnt_pre_reasoning"),
            2,
        )
        self.assertEqual(
            getattr(agent, "cnt_post_reasoning"),
            2,
        )
        self.assertEqual(
            getattr(agent, "cnt_pre_acting"),
            2,
        )
        self.assertEqual(
            getattr(agent, "cnt_post_acting"),
            2,
        )
