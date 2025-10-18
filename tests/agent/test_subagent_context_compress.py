# -*- coding: utf-8 -*-
"""Ensure context compression triggers only on host side."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    CountingSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_context_compress_called_once() -> None:
    """Host should trigger context compression exactly once per call."""

    async def _run() -> None:
        agent = build_host_agent()
        CountingSubAgent.reset()

        tool_name = await agent.register_subagent(
            CountingSubAgent,
            build_spec("counter"),
        )

        assert CountingSubAgent.compress_calls == 0

        await agent.memory.add(
            Msg(
                name="human",
                content="Count context compress invocation.",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="ctx-1",
            name=tool_name,
            input={"task_summary": "tag=count"},
        )

        await invoke_tool(agent, tool_call)

        assert CountingSubAgent.compress_calls == 1

    asyncio.run(_run())
