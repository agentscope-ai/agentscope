# -*- coding: utf-8 -*-
"""Regression tests for in-progress tool results in agent state."""
import unittest

from agentscope.message import ToolCallBlock, ToolResultBlock, ToolResultState
from agentscope.state import AgentState


class RunningToolResultTest(unittest.TestCase):
    """Partial results must not complete a tool call."""

    def test_running_then_terminal_results(self) -> None:
        """A call stays unfinished until its result ends."""
        for terminal in (
            ToolResultState.SUCCESS,
            ToolResultState.ERROR,
            ToolResultState.INTERRUPTED,
            ToolResultState.DENIED,
        ):
            with self.subTest(terminal=terminal):
                state = AgentState()
                call = ToolCallBlock(
                    id="call", name="demo", input="{}", state="submitted"
                )
                result = ToolResultBlock(
                    id="call", name="demo", output="partial", state="running"
                )
                state.append_context("agent", [call, result])
                self.assertEqual(
                    state.get_awaiting_tool_calls("agent"), [call]
                )
                self.assertEqual(
                    state.get_unfinished_tool_calls("agent"), [call]
                )
                result.state = terminal
                self.assertEqual(state.get_awaiting_tool_calls("agent"), [])
                self.assertEqual(state.get_unfinished_tool_calls("agent"), [])
