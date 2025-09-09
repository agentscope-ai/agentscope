# -*- coding: utf-8 -*-
"""End-to-end live test for Autonomous MemTool agent (Gemini/OpenAI/DashScope).

Usage:
  - Set a model key (e.g., GEMINI_API_KEY) and run:
      python -m examples.memtool_demo.live_test
"""
from __future__ import annotations

import asyncio
import os

import agentscope
from agentscope.message import Msg

from examples.memtool_demo.agentic_mode import build_autonomous_agent, DEFAULT_TOOL_BUDGET


async def main() -> None:
    # Verbose logging to stdout
    agentscope.setup_logger(level="DEBUG")

    domain = os.getenv("AGENT_DOMAIN_EXPERTISE", "File Ops & Math")
    try:
        budget = int(os.getenv("AGENT_MAX_TOOL_COUNT", "2"))
    except ValueError:
        budget = 2

    agent = build_autonomous_agent(
        domain_expertise=domain,
        max_tool_count=budget,
    )

    print("===== Turn 1: Compute and write file =====")
    msg1 = Msg(
        "user",
        "请计算 13*7 的结果，把结果写入 notes.txt，然后简要说明你做了什么。",
        role="user",
    )
    res1 = await agent(msg1)
    try:
        print("\n[Turn 1 Final]:", res1.get_text_content())
    except Exception:
        print("\n[Turn 1 Final - raw]:", getattr(res1, "content", None))

    print("\n===== Turn 2: Search file, encourage pruning =====")
    msg2 = Msg(
        "user",
        "现在不要数学计算，只需在 notes.txt 中搜索 '91' 并返回匹配的行号。开始前请清理不需要的工具。",
        role="user",
    )
    res2 = await agent(msg2)
    try:
        print("\n[Turn 2 Final]:", res2.get_text_content())
    except Exception:
        print("\n[Turn 2 Final - raw]:", getattr(res2, "content", None))


if __name__ == "__main__":
    asyncio.run(main())
