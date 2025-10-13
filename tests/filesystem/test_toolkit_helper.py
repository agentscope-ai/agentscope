# -*- coding: utf-8 -*-
"""Tests for the BuiltinFileSystem × Toolkit helper (closure-only route)."""
from __future__ import annotations

import asyncio
import inspect  # noqa: F401  # reserved for signature assertions if needed

import pytest

# Ensure optional runtime deps exist before importing agentscope modules
pytest.importorskip("shortuuid")

def test_helper_registers_closure_tools_and_schema_has_no_handle() -> None:
    from agentscope.filesystem._toolkit_helper import create_filesystem_toolkit
    fs, tk = create_filesystem_toolkit()

    # The helper should register the six tools by closure; check names exist
    schemas = tk.get_json_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {
        "ws_list",
        "ws_file",
        "ws_read_file",
        "ws_read_re",
        "ws_write",
        "ws_delete",
    }.issubset(names)

    # Schema properties contain only JSON args; must not leak internal handle
    for s in schemas:
        props = s["function"]["parameters"].get("properties", {})
        assert "handle" not in props


def test_helper_tools_execute_end_to_end() -> None:
    async def run_case() -> None:
        from agentscope.filesystem._toolkit_helper import create_filesystem_toolkit
        fs, tk = create_filesystem_toolkit()
        # Call ws_file on the seeded hello.txt to verify closure works
        tool_call = {
            "type": "tool_use",
            "id": "t1",
            "name": "ws_file",
            "input": {"path": "/workspace/hello.txt"},
        }
        agen = await tk.call_tool_function(tool_call)  # type: ignore[arg-type]
        last = None
        async for chunk in agen:
            last = chunk
        assert last is not None
        assert any(
            b.get("type") == "text" and "/workspace/hello.txt" in b.get("text", "")
            for b in last.content
        )

    asyncio.run(run_case())
