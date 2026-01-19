# -*- coding: utf-8 -*-
"""Subagent memory isolation tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    EchoSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_subagent_memory_isolation() -> None:
    """Ensure subagent context injection does not leak into host memory."""

    async def _run() -> None:
        agent = build_host_agent()
        EchoSubAgent.reset()

        tool_name = await agent.register_subagent(
            EchoSubAgent,
            build_spec("recorder"),
        )

        user_msg = Msg(
            name="human",
            content="Summarize the latest updates.",
            role="user",
        )
        await agent.memory.add(user_msg)
        original_size = await agent.memory.size()

        tool_call = ToolUseBlock(
            type="tool_use",
            id="call-2",
            name=tool_name,
            input={"message": "Summarize the latest updates."},
        )

        await invoke_tool(agent, tool_call)

        assert await agent.memory.size() == original_size
        assert (
            EchoSubAgent.delegation_payloads[0]["input_payload"]["message"]
            == "Summarize the latest updates."
        )

        refreshed_history = await agent.memory.get_memory()
        assert refreshed_history[-1].metadata is not None
        assert (
            refreshed_history[-1].metadata["delegation_context"][
                "input_payload"
            ]["message"]
            == "Summarize the latest updates."
        )

    asyncio.run(_run())
