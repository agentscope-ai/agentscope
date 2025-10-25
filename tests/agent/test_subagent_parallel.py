# -*- coding: utf-8 -*-
"""Parallel subagent execution tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    ParallelSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_subagent_parallel_calls() -> None:
    """Ensure parallel delegation keeps per-subagent isolation."""

    async def _run() -> None:
        agent = build_host_agent(parallel=True)
        ParallelSubAgent.reset()

        tool_a = await agent.register_subagent(
            ParallelSubAgent,
            build_spec("alpha"),
        )
        tool_b = await agent.register_subagent(
            ParallelSubAgent,
            build_spec("beta"),
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Run parallel tasks.",
                role="user",
            ),
        )

        async def invoke(tool_name: str, tag: str, delay: float) -> None:
            tool_call = ToolUseBlock(
                type="tool_use",
                id=f"parallel-{tag}",
                name=tool_name,
                input={"message": f"run {tag}", "tag": tag, "delay": delay},
            )
            await invoke_tool(agent, tool_call)

        await asyncio.gather(
            invoke(tool_a, "task-a", 0.05),
            invoke(tool_b, "task-b", 0.01),
        )

        assert ParallelSubAgent.memory_sizes == [1, 1]
        assert set(ParallelSubAgent.order) == {"task-a", "task-b"}

    asyncio.run(_run())
