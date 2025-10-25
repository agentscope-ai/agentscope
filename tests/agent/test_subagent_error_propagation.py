# -*- coding: utf-8 -*-
"""Subagent failure propagation tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    FailingSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_subagent_error_propagation() -> None:
    """Subagent exceptions must be wrapped into ToolResponse metadata."""

    async def _run() -> None:
        agent = build_host_agent()

        tool_name = await agent.register_subagent(
            FailingSubAgent,
            build_spec("failing"),
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Do the impossible.",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="call-3",
            name=tool_name,
            input={"message": "Do the impossible."},
        )

        response = await invoke_tool(agent, tool_call)

        assert response.metadata is not None
        assert response.metadata["unavailable"] is True
        assert "delegation failed" in response.metadata["error"]
        assert response.metadata["subagent"] == "failing"
        assert response.metadata["supervisor"] == agent.name
        assert response.is_last is True

    asyncio.run(_run())
