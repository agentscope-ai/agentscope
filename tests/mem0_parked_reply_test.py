# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Tests that mem0 does not persist a reply parked on permission.

A parked reply ends the stream with a placeholder assistant message instead of
an answer, so writing it back would store a non-fact in long-term memory.
"""
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.middleware import Mem0Middleware
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit

TOOL_NAME = "ask_permission_tool"
TOOL_INPUT = "remind"


class _FakeAsyncMem0Client:
    """Records the searches and writes the middleware performs."""

    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.add_calls: list[dict] = []

    async def search(self, query: str, **kwargs: Any) -> Any:
        """Pretend mem0 returned one memory."""
        self.search_calls.append({"query": query, **kwargs})
        return {"results": [{"memory": "alice loves coffee"}]}

    async def add(self, messages: list, **kwargs: Any) -> None:
        """Record the write."""
        self.add_calls.append({"messages": messages, **kwargs})


class _AskPermissionTool(ToolBase):
    """A tool that always requires user confirmation."""

    name: str = TOOL_NAME
    description: str = "A tool that needs the user's permission."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always ask the user first."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="This tool needs confirmation",
            message="This tool needs confirmation",
        )

    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Echo the input back."""
        return ToolChunk(content=[TextBlock(text=f"ran: {input}")])


class Mem0ParkedReplyTest(IsolatedAsyncioTestCase):
    """A parked reply must not reach long-term memory."""

    def _tool_call_response(self, is_last: bool) -> ChatResponse:
        """Build one scripted tool-call chunk."""
        return ChatResponse(
            content=[
                ToolCallBlock(
                    id="call-1",
                    name=TOOL_NAME,
                    input=json.dumps({"input": TOOL_INPUT}),
                ),
            ],
            is_last=is_last,
        )

    async def test_parked_reply_is_not_written_back(self) -> None:
        """A reply stopped on a permission prompt writes nothing."""
        model = MockModel(context_size=100_000)
        model.set_responses(
            [
                [
                    self._tool_call_response(False),
                    self._tool_call_response(True),
                ],
            ],
        )
        fake = _FakeAsyncMem0Client()
        middleware = Mem0Middleware(
            client=fake,
            user_id="alice",
            agent_id="agent_under_test",
            mode="static_control",
        )
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=model,
            toolkit=Toolkit(tools=[_AskPermissionTool()]),
            middlewares=[middleware],
        )

        async for _ in agent.reply_stream(
            UserMsg("user", "remind me what I like"),
        ):
            pass

        # The retrieval half still ran, so the middleware is active.
        self.assertEqual(len(fake.search_calls), 1)
        # The reply parked on the permission prompt, so nothing was stored.
        self.assertEqual(fake.add_calls, [])

    async def test_finished_reply_is_still_written_back(self) -> None:
        """A completed reply is still persisted, guarding the fix."""
        model = MockModel(context_size=100_000)
        model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="hi alice")],
                    is_last=True,
                ),
            ],
        )
        fake = _FakeAsyncMem0Client()
        middleware = Mem0Middleware(
            client=fake,
            user_id="alice",
            agent_id="agent_under_test",
            mode="static_control",
        )
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=model,
            toolkit=Toolkit(),
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "remind me what I like"))

        self.assertEqual(len(fake.add_calls), 1)
        self.assertEqual(
            fake.add_calls[0]["messages"],
            [
                {"role": "user", "content": "remind me what I like"},
                {"role": "assistant", "content": "hi alice"},
            ],
        )
