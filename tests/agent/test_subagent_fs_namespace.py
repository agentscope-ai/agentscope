# -*- coding: utf-8 -*-
"""Filesystem namespace enforcement tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    NamespaceSubAgent,
    attach_filesystem,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_subagent_filesystem_namespace() -> None:
    """Subagent must be constrained to its delegated namespace."""

    async def _run() -> None:
        agent = build_host_agent()
        NamespaceSubAgent.reset()
        attach_filesystem(agent)

        tool_name = await agent.register_subagent(
            NamespaceSubAgent,
            build_spec("fs"),
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Write diagnostics to disk.",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="fs-1",
            name=tool_name,
            input={"message": "Write diagnostics to disk.", "tag": "fs"},
        )

        await invoke_tool(agent, tool_call)

        assert NamespaceSubAgent.writes == [
            "/workspace/subagents/fs/artifact.txt",
        ]
        assert NamespaceSubAgent.errors == ["AccessDeniedError"]

    asyncio.run(_run())
