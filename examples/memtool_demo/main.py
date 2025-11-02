# -*- coding: utf-8 -*-
"""MemTool-style dynamic tool management demo with AgentScope.

Usage:
    python examples/memtool_demo/main.py "请计算 2+2 和 3*7 并把结果保存到 notes.txt"
"""
from __future__ import annotations

import asyncio
import sys

from agentscope import logger

from memtool_components import (
    build_default_tool_kb,
    MemToolManager,
    Orchestrator,
)


async def main(user_query: str) -> None:
    kb = build_default_tool_kb()
    manager = MemToolManager(kb, budget=5)
    orchestrator = Orchestrator(manager)

    logger.info("[MemTool] Received query: %s", user_query)
    res = await orchestrator.run(user_query)
    logger.info("[MemTool] Final response: %s", res.get_text_content())


if __name__ == "__main__":
    query = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "请计算 2+2 和 3*7 并把结果保存到 notes.txt"
    )
    try:
        asyncio.run(main(query))
    except KeyboardInterrupt:
        pass
