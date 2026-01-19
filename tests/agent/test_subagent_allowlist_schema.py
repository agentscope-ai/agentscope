# -*- coding: utf-8 -*-
"""Subagent allowlist and schema tests."""
from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

from agentscope.message import Msg, TextBlock, ToolUseBlock
from ._shared import (
    AllowlistSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)

if TYPE_CHECKING:  # pragma: no cover
    from agentscope.tool import ToolResponse


async def _allowed_tool(prompt: str = "") -> ToolResponse:
    from agentscope.tool import ToolResponse

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"allowed:{prompt}",
            ),
        ],
        is_last=True,
    )


async def _forbidden_tool() -> ToolResponse:
    from agentscope.tool import ToolResponse

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="forbidden",
            ),
        ],
        is_last=True,
    )


def test_subagent_allowlist_schema() -> None:
    """Verify allowlist cloning and schema exposure."""

    async def _run() -> None:
        agent = build_host_agent()
        AllowlistSubAgent.reset()

        agent.toolkit.register_tool_function(_allowed_tool)
        agent.toolkit.register_tool_function(_forbidden_tool)

        spec = build_spec("allowlist")
        # new API: pass tool functions directly
        # use host toolkit registrations to fetch the original function
        allowed = agent.toolkit.tools["_allowed_tool"].original_func
        spec.tools = [allowed]

        tool_name = await agent.register_subagent(
            AllowlistSubAgent,
            spec,
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Use the allowlisted helper.",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="allow-1",
            name=tool_name,
            input={
                "message": "Use the allowlisted helper.",
                "tag": "allow",
                "delay": 0.0,
            },
        )

        await invoke_tool(agent, tool_call)

        assert AllowlistSubAgent.seen_tools == [{"_allowed_tool"}]

        schema_names = {
            schema["function"]["name"]
            for schema in agent.toolkit.get_json_schemas()
        }
        assert tool_name in schema_names

        registered_schema = next(
            schema
            for schema in agent.toolkit.get_json_schemas()
            if schema["function"]["name"] == tool_name
        )
        params = registered_schema["function"]["parameters"]
        assert "message" in params.get("properties", {})

    asyncio.run(_run())
