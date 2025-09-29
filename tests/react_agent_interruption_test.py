# -*- coding: utf-8 -*-
"""The ReAct agent unittests."""
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
import asyncio

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import TextBlock, ToolUseBlock, Msg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit, ToolResponse


class MyToolCallModel(ChatModelBase):
    """Test model class."""

    def __init__(self) -> None:
        """Initialize the test model."""
        super().__init__("test_model", stream=False)
        self.fake_content = [
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="interrupted_tool_call_func",
                input={},
            ),
        ]

    async def __call__(
        self,
        _messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock model call with fake tool call."""
        return ChatResponse(
            content=self.fake_content,
        )


async def interrupted_tool_call_func() -> AsyncGenerator[ToolResponse, None]:
    """This is a dummy tool for testing tool interruption"""
    dummy_output = "testing interrupted tool call"
    for i, _ in enumerate(dummy_output):
        if i > 0 and i % 2 == 0:
            yield ToolResponse(
                content=[TextBlock(type="text", text=dummy_output[:i])],
                is_last=False,
            )
        if i > 6:
            raise asyncio.CancelledError()


class ReActAgentToolInterruptionTest(IsolatedAsyncioTestCase):
    """Test class for ReActAgent."""

    async def test_react_agent_interruption_break(self) -> None:
        """Test the ReActAgent break ReAct loop with tool interruption."""
        model = MyToolCallModel()
        toolkit = Toolkit()
        toolkit.register_tool_function(interrupted_tool_call_func)
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday.",
            model=model,
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
            agent_handle_tool_interruption=False,  # disable
        )
        res = await agent(Msg(name="user", content="test", role="assistant"))
        # agent should use `handle_interrupt` function to response
        self.assertEqual(
            res.content,
            (
                "I noticed that you have interrupted me. "
                "What can I do for you?"
            ),
        )

    async def test_react_agent_interruption_agent_handle(self) -> None:
        """Test the ReActAgent handling tool interruption."""
        model = MyToolCallModel()
        toolkit = Toolkit()
        toolkit.register_tool_function(interrupted_tool_call_func)
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday.",
            model=model,
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
            # agent_handle_tool_interruption=True, # enable by default
            max_iters=1,
        )
        res = await agent(Msg(name="user", content="test", role="assistant"))
        model_res = await MyToolCallModel()([])
        # agent should call _summary and return the model generated content
        self.assertEqual(res.content, model_res.content)
