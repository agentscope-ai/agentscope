# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Test that a tool group owns the lists it is constructed with."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.mcp import MCPClient
from agentscope.message import TextBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, ToolGroup, Toolkit


class Tool1(ToolBase):
    """A simple tool for testing."""

    name: str = "tool_1"
    description: str = "A simple tool for testing."
    input_schema: dict = {
        "type": "object",
        "properties": {},
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Do you want to use tool_1?",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Run the tool."""
        return ToolChunk(content=[TextBlock(text="from tool_1")])


class Tool2(ToolBase):
    """Another simple tool for testing."""

    name: str = "tool_2"
    description: str = "Another simple tool for testing."
    input_schema: dict = {
        "type": "object",
        "properties": {},
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Do you want to use tool_2?",
        )

    async def call(self, **kwargs: Any) -> ToolChunk:
        """Run the tool."""
        return ToolChunk(content=[TextBlock(text="from tool_2")])


class ToolGroupOwnershipTest(IsolatedAsyncioTestCase):
    """The caller's lists must not be aliased by the tool group."""

    async def test_lists_are_copied(self) -> None:
        """A group keeps its own lists, since ``add_tool`` appends to them
        in place."""
        tools = [Tool1()]
        mcps: list[MCPClient] = []
        group = ToolGroup(
            name="group_1",
            description="Group 1",
            tools=tools,
            mcps=mcps,
        )
        self.assertIsNot(group.tools, tools)
        self.assertIsNot(group.mcps, mcps)

        toolkit = Toolkit(tool_groups=[group])
        await toolkit.add_tool(Tool2(), group_name="group_1")

        self.assertEqual([_.name for _ in tools], ["tool_1"])
        self.assertEqual(
            [_.name for _ in group.tools],
            ["tool_1", "tool_2"],
        )

    async def test_toolkits_sharing_a_list_are_independent(self) -> None:
        """Two toolkits built from one list must not leak tools into each
        other."""
        shared = [Tool1()]
        toolkit_a = Toolkit(tools=shared)
        toolkit_b = Toolkit(tools=shared)

        await toolkit_a.add_tool(Tool2())

        schemas_a = await toolkit_a.get_tool_schemas()
        schemas_b = await toolkit_b.get_tool_schemas()
        self.assertEqual(
            [_["function"]["name"] for _ in schemas_a],
            ["tool_1", "tool_2"],
        )
        self.assertEqual(
            [_["function"]["name"] for _ in schemas_b],
            ["tool_1"],
        )
