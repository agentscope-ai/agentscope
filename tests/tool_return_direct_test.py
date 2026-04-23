# -*- coding: utf-8 -*-
"""Tests for tool return_direct behavior."""
from unittest import IsolatedAsyncioTestCase

from agentscope.tool import ToolResponse, Toolkit
from agentscope.message import TextBlock


def _answer_tool(answer: str) -> ToolResponse:
    """Final answer tool for testing."""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"final:{answer}",
            ),
        ],
    )


class TestToolReturnDirect(IsolatedAsyncioTestCase):
    """Test return_direct configuration behavior."""

    async def test_return_direct_basic(self) -> None:
        """return_direct should be stored on registered tool."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _answer_tool,
            func_name="answer",
            return_direct=True,
        )
        self.assertTrue(toolkit.tools["answer"].return_direct)

    async def test_return_direct_default_false(self) -> None:
        """return_direct should default to False."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _answer_tool,
            func_name="answer",
        )
        self.assertFalse(toolkit.tools["answer"].return_direct)

    async def test_return_direct_false_explicit(self) -> None:
        """Explicit False should be kept."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _answer_tool,
            func_name="answer",
            return_direct=False,
        )
        self.assertFalse(toolkit.tools["answer"].return_direct)
