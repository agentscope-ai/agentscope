# -*- coding: utf-8 -*-
"""Offline smoke tests for MemTool demo components.

Validates:
- ToolKB lexical search and RRF fallback (no embeddings -> lexical only)
- ToolManagerService dynamic equip/remove without touching any LLM

Run:
    python examples/memtool_demo/smoke_test.py
"""
from __future__ import annotations

import asyncio

from agentscope.tool import Toolkit

try:
    from examples.memtool_demo.memtool_components import (
        build_default_tool_kb,
        ToolKB,
    )
    from examples.memtool_demo.agentic_mode import (
        ToolManagerService,
        DEFAULT_TOOL_BUDGET,
    )
except Exception:
    from memtool_components import (
        build_default_tool_kb,
        ToolKB,
    )  # type: ignore
    from agentic_mode import (
        ToolManagerService,
        DEFAULT_TOOL_BUDGET,
    )  # type: ignore


async def test_kb_search() -> None:
    kb: ToolKB = build_default_tool_kb()
    # Lexical search should find math + file tools for typical queries
    q1 = "math calculate arithmetic"
    lex = kb.search_lexical(q1, top_k=5)
    assert isinstance(lex, list) and len(lex) > 0, "Lexical search returned nothing"

    # RRF with no embeddings configured should fall back to lexical-only ranking
    rrf = await kb.search_rrf(q1, top_k=5)
    assert isinstance(rrf, list) and len(rrf) > 0, "RRF search returned nothing"

    print("KB lexical:", lex)
    print("KB rrf (may == lexical when no embeddings):", rrf)


async def test_manager_equip_remove() -> None:
    kb = build_default_tool_kb()
    tk = Toolkit()
    manager = ToolManagerService(tk, budget=3)

    # Equip tools
    equip_res = await manager.search_tools(["math", "write file"], top_k=5)
    assert "Equipped tools:" in equip_res.content[0]["text"], equip_res
    equipped_after = manager.list_equipped_tools()
    print("Equipped after search:", equipped_after)
    assert equipped_after != "<empty>", "No tools equipped"

    # Remove a tool (try the first equipped one)
    first = equipped_after.split(", ")[0]
    rm_res = manager.remove_tools([first])
    assert "Removed:" in rm_res.content[0]["text"] or "No matching" in rm_res.content[0]["text"], rm_res
    print("After remove:", manager.list_equipped_tools())


async def main() -> None:
    await test_kb_search()
    await test_manager_equip_remove()
    print("Smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
