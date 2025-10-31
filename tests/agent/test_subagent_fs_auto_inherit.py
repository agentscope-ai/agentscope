# -*- coding: utf-8 -*-
"""Auto-inherit full filesystem tools from Host to SubAgent."""
from __future__ import annotations

import asyncio

from agentscope.message import Msg, ToolUseBlock
from ._shared import (
    AllowlistSubAgent,
    build_host_agent,
    build_spec,
    attach_filesystem,
    invoke_tool,
)


def test_subagent_fs_auto_inherit() -> None:
    async def _run() -> None:
        agent = build_host_agent()
        attach_filesystem(agent)
        AllowlistSubAgent.reset()

        tool_name = await agent.register_subagent(
            AllowlistSubAgent,
            build_spec("fsauto"),
        )

        await agent.memory.add(
            Msg(name="human", content="check fs tools", role="user"),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id="fs-auto-1",
            name=tool_name,
            input={"message": "check fs tools", "tag": "fsauto"},
        )
        await invoke_tool(agent, tool_call)

        seen = AllowlistSubAgent.seen_tools[-1]
        # At least a few canonical FS tools should be present
        assert "list_directory" in seen
        assert "get_file_info" in seen
        assert "read_text_file" in seen
        assert "write_file" in seen
        assert "delete_file" in seen

    asyncio.run(_run())
