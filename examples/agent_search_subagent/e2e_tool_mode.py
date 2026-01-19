# -*- coding: utf-8 -*-
"""E2E (tool mode) for SearchSubReactAgent via Host/Toolkit.

Flow:
- make_subagent_tool(SearchSubReactAgent, SubAgentSpec(...))
- host.toolkit.register_tool_function(..., group_name="search")
- Host.ReActAgent produces tool_use → Toolkit.call_tool_function →
  export_agent(..., model_override=host.model)
- delegate(...) folds Msg → ToolResponse → Host returns final Msg

Requirements:
- OPENAI_API_KEY set for OpenAIChatModel
- Optional: playwright + webkit if Bing/Sogou/GitHub are selected; Wiki path
  works without browser
"""

from __future__ import annotations

import asyncio

import os
from pathlib import Path

from src.agentscope.agent import ReActAgent
from src.agentscope.model import OpenAIChatModel
from src.agentscope.formatter import OpenAIChatFormatter
from src.agentscope.memory import InMemoryMemory
from src.agentscope.tool import Toolkit
from src.agentscope.message import Msg

from src.agentscope.agent._subagent_tool import (
    SubAgentSpec,
    make_subagent_tool,
)
from examples.agent_search_subagent.search_subreact_agent import (
    SearchSubReactAgent,
    get_all_search_tools,
)


def _load_env_minimal() -> None:
    "+" "Load .env into os.environ (minimal, no external deps)." ""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


async def main() -> None:
    # 0) 自动加载 .env 并校验关键变量（按铁律）
    _load_env_minimal()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required; place it in environment or .env",
        )
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    # 1) Host with real ChatModel (no mocks)
    host = ReActAgent(
        name="Host",
        sys_prompt=(
            "You are a research assistant. "
            "When the user's question requires web knowledge, "
            "you MUST call the tool `search_web` with an appropriate query, "
            "then synthesize a concise answer."
        ),
        model=OpenAIChatModel(
            model_name=model_name,
            stream=False,
            client_args={"base_url": base_url} if base_url else None,
        ),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=Toolkit(),
        parallel_tool_calls=False,
    )

    # 2) Create search tool group and register subagent tool (provider-only to
    # avoid recursion)
    host.toolkit.create_tool_group(
        "search",
        description=(
            "Web search tools exposed via a subagent (wiki/bing/sogou/github)"
        ),
        active=True,
    )

    spec = SubAgentSpec(
        name="search_web",
        tools=get_all_search_tools(),  # provider-only
    )

    tool_fn, tool_schema = await make_subagent_tool(
        SearchSubReactAgent,
        spec,
        host=host,
        tool_name="search_web",
    )

    host.toolkit.register_tool_function(
        tool_fn,
        group_name="search",
        json_schema=tool_schema["json_schema"],
    )

    # 3) Validate schema zero-deviation: {query} only
    schemas = host.toolkit.get_json_schemas()
    search_schema = next(
        s for s in schemas if s["function"]["name"] == "search_web"
    )
    props = search_schema["function"]["parameters"].get("properties", {})
    required = search_schema["function"]["parameters"].get("required", [])
    assert set(props.keys()) == {
        "query",
    }, f"unexpected properties: {set(props.keys())}"
    assert required == ["query"], f"unexpected required: {required}"

    # 4) Run: host should call search_web then return a final answer
    query = "Alan Turing biography key facts"
    reply = await host(Msg("user", query, "user"))
    text = reply.get_text_content() or ""
    print("=== search_web(tool mode) → Host reply (first 400 chars) ===")
    print(text[:400])
    print("\n=== END ===")


if __name__ == "__main__":
    asyncio.run(main())
