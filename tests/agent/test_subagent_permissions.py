# -*- coding: utf-8 -*-
"""Permission bundle propagation tests."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    PermissionSubAgent,
    build_host_agent,
    build_spec,
    invoke_tool,
)


def test_permission_handles_copied_from_host() -> None:
    """Subagent must receive the host's session/tracer handles."""

    async def _run() -> None:
        agent = build_host_agent()
        PermissionSubAgent.reset()

        agent.session = object()
        agent.tracer = object()

        tool_name = await agent.register_subagent(
            PermissionSubAgent,
            build_spec("permission"),
        )

        await agent.memory.add(
            Msg(
                name="human",
                content="Check permission bundle",
                role="user",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="perm-1",
            name=tool_name,
            input={"message": "Check permission bundle", "tag": "perm"},
        )

        await invoke_tool(agent, tool_call)

        permissions = PermissionSubAgent.permissions_snapshot
        assert permissions is not None
        assert permissions.session is agent.session
        assert permissions.tracer is agent.tracer

    asyncio.run(_run())
