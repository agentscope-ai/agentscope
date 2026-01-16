# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os

from agentscope.filesystem import DiskFileSystem
from agentscope.filesystem._service import FileDomainService
from agentscope.tool._toolkit import Toolkit


def test_tools_register_and_execute_end_to_end(tmp_path) -> None:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        fs = DiskFileSystem()
        handle = fs.create_handle(
            grants=[
                {
                    "prefix": "/userinput/",
                    "ops": {"list", "file", "read_file"},
                },
                {
                    "prefix": "/workspace/",
                    "ops": {"list", "file", "read_file", "write", "delete"},
                },
            ],
        )
        svc = FileDomainService(handle)

        # seed corpus
        # type: ignore[attr-defined]
        corpus_dir = fs._userinput_dir
        with open(
            os.path.join(corpus_dir, "corpus.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("alpha\n")

        tk = Toolkit()
        for func, svc2 in fs.get_tools(svc):
            tk.register_tool_function(func, preset_kwargs={"service": svc2})

        async def run() -> None:
            # write workspace file
            call = {
                "type": "tool_use",
                "id": "t1",
                "name": "write_file",
                "input": {"path": "/workspace/a.txt", "content": "hello"},
            }
            agen = await tk.call_tool_function(call)  # type: ignore[arg-type]
            async for _ in agen:
                pass

            # list workspace
            call = {
                "type": "tool_use",
                "id": "t2",
                "name": "list_directory",
                "input": {"path": "/workspace/"},
            }
            agen = await tk.call_tool_function(call)  # type: ignore[arg-type]
            out = None
            async for chunk in agen:
                out = chunk
            assert out and any(
                "a.txt" in b.get("text", "")
                for b in out.content
                if b.get("type") == "text"
            )

        asyncio.run(run())
    finally:
        os.chdir(cwd)
