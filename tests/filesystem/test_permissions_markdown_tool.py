# -*- coding: utf-8 -*-
"""E2E test for fs_describe_permissions_markdown tool via Toolkit."""
from __future__ import annotations

import asyncio

from agentscope.tool import Toolkit
from agentscope.message import ToolUseBlock

from agentscope.filesystem._memory import InMemoryFileSystem
from agentscope.filesystem._service import FileDomainService
from agentscope.filesystem._tools import fs_describe_permissions_markdown


def test_fs_describe_permissions_markdown_tool() -> None:
    async def _run() -> None:
        fs = InMemoryFileSystem()
        handle = fs.create_handle(
            [
                {"prefix": "/proc/", "ops": {"list", "file", "read_file"}},
                {
                    "prefix": "/tmp/",
                    "ops": {"list", "file", "read_file", "write", "delete"},
                },
            ]
        )
        svc = FileDomainService(handle)

        tk = Toolkit()
        tk.register_tool_function(
            fs_describe_permissions_markdown,
            preset_kwargs={"service": svc},
        )

        res_gen = await tk.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="1",
                name="fs_describe_permissions_markdown",
                input={},
            ),
        )
        chunks = [chunk async for chunk in res_gen]
        assert len(chunks) == 1
        text = chunks[0].content[0]["text"]
        assert (
            text
            == "/proc/: ls, stat, read\n/tmp/: ls, stat, read, write, delete"
        )

    asyncio.run(_run())
