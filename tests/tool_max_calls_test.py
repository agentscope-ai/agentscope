# -*- coding: utf-8 -*-
"""Tests for tool max_calls behavior."""
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolUseBlock
from agentscope.tool import ToolResponse, Toolkit


def _echo_tool(query: str) -> ToolResponse:
    """Simple tool for testing."""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"echo:{query}",
            ),
        ],
    )


async def _last_chunk_text(
    toolkit: Toolkit,
    tool_name: str,
    payload: dict,
) -> str:
    """Execute tool call and return the last chunk's first text content."""
    tool_call = ToolUseBlock(
        type="tool_use",
        id="test_id",
        name=tool_name,
        input=payload,
    )
    last_chunk = None
    async for chunk in await toolkit.call_tool_function(tool_call):
        last_chunk = chunk
    assert last_chunk is not None
    return last_chunk.content[0]["text"]


class TestToolMaxCalls(IsolatedAsyncioTestCase):
    """Test max_calls behavior."""

    async def test_max_calls_basic(self) -> None:
        """Tool should reject calls after max_calls is reached."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _echo_tool,
            func_name="limited_search",
            max_calls=2,
        )

        text1 = await _last_chunk_text(
            toolkit,
            "limited_search",
            {"query": "a"},
        )
        text2 = await _last_chunk_text(
            toolkit,
            "limited_search",
            {"query": "b"},
        )
        text3 = await _last_chunk_text(
            toolkit,
            "limited_search",
            {"query": "c"},
        )

        self.assertEqual(text1, "echo:a")
        self.assertEqual(text2, "echo:b")
        self.assertIn("ToolCallLimitError", text3)
        self.assertIn("limited_search", text3)
        self.assertIn("(2)", text3)

    async def test_max_calls_validation_negative(self) -> None:
        """Negative max_calls should raise validation error."""
        toolkit = Toolkit()
        with self.assertRaises(ValueError):
            toolkit.register_tool_function(
                _echo_tool,
                func_name="bad_limit",
                max_calls=-1,
            )

    async def test_max_calls_zero(self) -> None:
        """max_calls=0 should reject the first call directly."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _echo_tool,
            func_name="zero_limit",
            max_calls=0,
        )
        text = await _last_chunk_text(
            toolkit,
            "zero_limit",
            {"query": "x"},
        )
        self.assertIn("ToolCallLimitError", text)
        self.assertIn("(0)", text)

    async def test_max_calls_unlimited(self) -> None:
        """max_calls=None should allow repeated calls."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _echo_tool,
            func_name="unlimited_search",
            max_calls=None,
        )

        for i in range(5):
            text = await _last_chunk_text(
                toolkit,
                "unlimited_search",
                {"query": str(i)},
            )
            self.assertEqual(text, f"echo:{i}")

    async def test_max_calls_increment(self) -> None:
        """call_count should be incremented for limited tools."""
        toolkit = Toolkit()
        toolkit.register_tool_function(
            _echo_tool,
            func_name="counted_search",
            max_calls=3,
        )
        self.assertEqual(toolkit.tools["counted_search"].call_count, 0)

        await _last_chunk_text(
            toolkit,
            "counted_search",
            {"query": "1"},
        )
        self.assertEqual(toolkit.tools["counted_search"].call_count, 1)

        await _last_chunk_text(
            toolkit,
            "counted_search",
            {"query": "2"},
        )
        self.assertEqual(toolkit.tools["counted_search"].call_count, 2)
