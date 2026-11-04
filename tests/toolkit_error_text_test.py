# -*- coding: utf-8 -*-
"""Unittests for the error text a failing tool hands back to the agent."""
from typing import Callable
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit, ToolResponse


def _raise_bare_timeout() -> None:
    """A tool that fails with an exception carrying no message."""
    raise TimeoutError()


def _raise_with_message() -> None:
    """A tool that fails with an ordinary message."""
    raise ValueError("boom")


def _raise_group() -> None:
    """A tool that fails inside a task group."""
    raise ExceptionGroup(
        "unhandled errors",
        [KeyError("missing")],
    )  # noqa: B904


class TestToolkitErrorText(IsolatedAsyncioTestCase):
    """The error block must stay readable when the exception carries no
    message of its own."""

    async def _call(self, func: Callable[[], None]) -> tuple[str, str]:
        """Run one failing tool and return its error state and text."""
        toolkit = Toolkit(tools=[FunctionTool(func)])
        response = None
        async for chunk in toolkit.call_tool(
            ToolCallBlock(id="1", name=func.__name__, input="{}"),
            AgentState(),
        ):
            if isinstance(chunk, ToolResponse):
                response = chunk
        self.assertIsNotNone(response)
        assert response is not None
        return response.state.value, response.content[0].text

    async def test_bare_exception_names_its_type(self) -> None:
        """A message-less exception still has to name its type."""
        state, text = await self._call(_raise_bare_timeout)

        self.assertEqual(state, "error")
        self.assertEqual(text, "TimeoutError")

    async def test_exception_message_is_unchanged(self) -> None:
        """An exception with a message must keep it verbatim."""
        state, text = await self._call(_raise_with_message)

        self.assertEqual(state, "error")
        self.assertEqual(text, "boom")

    async def test_exception_group_reports_the_leaf(self) -> None:
        """Only the leaf cause of a group is actionable for the model."""
        state, text = await self._call(_raise_group)

        self.assertEqual(state, "error")
        self.assertEqual(text, "'missing'")
