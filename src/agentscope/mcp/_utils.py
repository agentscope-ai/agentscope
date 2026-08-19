# -*- coding: utf-8 -*-
"""Internal helpers shared by MCP integrations."""
import re


def build_mcp_tool_name(mcp_name: str, raw_tool_name: str) -> str:
    """Build a provider-safe model-facing MCP tool name.

    Args:
        mcp_name (`str`):
            The validated MCP server name.
        raw_tool_name (`str`):
            The tool name reported by the MCP server.

    Returns:
        `str`:
            The model-facing tool name.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "x", raw_tool_name)
    return f"mcp__{mcp_name}__{sanitized}"
