# -*- coding: utf-8 -*-
"""
Test cases for MCP error handling in Toolkit.

This module tests that when one MCP client fails to list tools,
other MCP clients can still be loaded successfully.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentscope.tool import Toolkit
from agentscope.mcp import MCPClient


@pytest.mark.asyncio
async def test_toolkit_with_single_mcp_failure():
    """Test that other MCP tools can still be loaded when one MCP fails."""

    # Create a normal MCP client
    normal_mcp = MagicMock(spec=MCPClient)
    normal_mcp.name = "normal-mcp"
    normal_mcp.is_stateful = False
    normal_mcp.is_connected = True

    # Mock normal MCP to return tool list
    normal_tool = MagicMock()
    normal_tool.name = "normal_tool"
    normal_tool.input_schema = None
    normal_tool.description = "A normal tool"
    normal_mcp.list_tools = AsyncMock(return_value=[normal_tool])

    # Create a faulty MCP client
    failed_mcp = MagicMock(spec=MCPClient)
    failed_mcp.name = "failed-mcp"
    failed_mcp.is_stateful = False
    failed_mcp.is_connected = True
    failed_mcp.list_tools = AsyncMock(
        side_effect=Exception("Connection failed")
    )

    # Create Toolkit with both normal and faulty MCP
    toolkit = Toolkit(
        tools=[],
        mcps=[normal_mcp, failed_mcp],
    )

    # Get available tools - should not raise exception
    available_tools = await toolkit._get_available_tools(groups=[])

    # Verify: normal MCP tools should be available
    assert "normal_tool" in available_tools

    # Verify: faulty MCP should not affect the overall process
    assert len(available_tools) >= 1

    # Verify: list_tools was called on both MCPs
    normal_mcp.list_tools.assert_called_once()
    failed_mcp.list_tools.assert_called_once()


@pytest.mark.asyncio
async def test_toolkit_with_all_mcp_failure():
    """Test that Agent can still respond when all MCPs fail."""

    # Create multiple faulty MCP clients
    failed_mcp1 = MagicMock(spec=MCPClient)
    failed_mcp1.name = "failed-mcp-1"
    failed_mcp1.is_stateful = False
    failed_mcp1.is_connected = True
    failed_mcp1.list_tools = AsyncMock(
        side_effect=Exception("Connection failed")
    )

    failed_mcp2 = MagicMock(spec=MCPClient)
    failed_mcp2.name = "failed-mcp-2"
    failed_mcp2.is_stateful = False
    failed_mcp2.is_connected = True
    failed_mcp2.list_tools = AsyncMock(side_effect=Exception("Auth failed"))

    # Create Toolkit
    toolkit = Toolkit(
        tools=[],
        mcps=[failed_mcp1, failed_mcp2],
    )

    # Get available tools - should not raise exception
    available_tools = await toolkit._get_available_tools(groups=[])

    # Verify: no external tools available (only builtin meta/skill tools),
    # but process is normal
    assert "normal_tool" not in available_tools

    # Verify: list_tools was called on both MCPs
    failed_mcp1.list_tools.assert_called_once()
    failed_mcp2.list_tools.assert_called_once()


@pytest.mark.asyncio
async def test_toolkit_with_no_mcp():
    """Test normal case when no MCP is configured."""

    # Create Toolkit without MCP
    toolkit = Toolkit(
        tools=[],
        mcps=[],
    )

    # Get available tools - basic group only, no MCP tools
    available_tools = await toolkit._get_available_tools(groups=[])

    # Should not have any tools (no builtin tools, no MCP tools)
    assert len(available_tools) == 0


@pytest.mark.asyncio
async def test_toolkit_with_mixed_tools_and_mcp_failure():
    """Test case with builtin tools and MCP failure."""

    # Create builtin tool
    builtin_tool = MagicMock()
    builtin_tool.name = "builtin_tool"
    builtin_tool.input_schema = None
    builtin_tool.description = "A builtin tool"

    # Create faulty MCP
    failed_mcp = MagicMock(spec=MCPClient)
    failed_mcp.name = "failed-mcp"
    failed_mcp.is_stateful = False
    failed_mcp.is_connected = True
    failed_mcp.list_tools = AsyncMock(
        side_effect=Exception("Connection failed")
    )

    # Create Toolkit
    toolkit = Toolkit(
        tools=[builtin_tool],
        mcps=[failed_mcp],
    )

    # Get available tools
    available_tools = await toolkit._get_available_tools(groups=[])

    # Verify: builtin tool should be available
    assert "builtin_tool" in available_tools

    # Verify: at least one tool available
    assert len(available_tools) >= 1


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
