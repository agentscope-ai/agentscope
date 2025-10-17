# -*- coding: utf-8 -*-
"""Subagent tool registration tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    EchoSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_host_without_direct_tools() -> None:
    """Host delegates work entirely through a subagent tool."""

    async def _run() -> None:
        agent = build_host_agent()
        EchoSubAgent.reset()

        tool_name = await agent.register_subagent(
            EchoSubAgent,
            build_spec("echo"),
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Need an echo.",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="call-1",
            name=tool_name,
            input={
                "task_summary": "Repeat the latest request verbatim.",
            },
        )

        response = await invoke_tool(agent, tool_call)

        assert response.metadata is not None
        assert response.metadata["subagent"] == "echo"
        assert response.metadata["supervisor"] == agent.name
        assert response.metadata["delegation_context"]["task_summary"] == (
            "Repeat the latest request verbatim."
        )
        assert response.content[0]["type"] == "text"
        assert response.content[0]["text"].startswith("echo:")
        assert response.is_last is True

        host_size = await agent.memory.size()
        assert host_size == 1
        assert EchoSubAgent.memory_events == [1]
        assert EchoSubAgent.console_states == [True]
        assert EchoSubAgent.queue_states == [True]

        tool_names = set(agent.toolkit.tools.keys())
        assert tool_name in tool_names
        assert agent.finish_function_name in tool_names

    asyncio.run(_run())
