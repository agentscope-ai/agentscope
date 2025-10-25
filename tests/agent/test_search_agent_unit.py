# -*- coding: utf-8 -*-
"""Unit tests for SearchAgent and SearchQuery."""
from __future__ import annotations

import asyncio
from time import time

from agentscope.agent import (
    SearchAgent,
    SearchQuery,
    SubAgentSpec,
    ReActAgent,
)
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, ToolUseBlock, TextBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit, ToolResponse


class StaticModel(ChatModelBase):
    """Deterministic chat model returning a no-op text chunk."""

    def __init__(self) -> None:
        super().__init__("static", stream=False)

    async def __call__(  # type: ignore[override]
        self,
        _messages,
        **_,
    ) -> ChatResponse:
        return ChatResponse(
            content=[TextBlock(type="text", text="noop")],
        )


def build_host_agent() -> ReActAgent:
    return ReActAgent(
        name="Supervisor",
        sys_prompt="You are a supervisor.",
        model=StaticModel(),
        formatter=DashScopeChatFormatter(),
        memory=InMemoryMemory(),
        toolkit=Toolkit(),
        parallel_tool_calls=False,
    )


def build_spec(name: str) -> SubAgentSpec:
    return SubAgentSpec(name=name)


async def _stub_search(tag: str, query: str) -> ToolResponse:
    return ToolResponse(
        content=[TextBlock(type="text", text=f"{tag}:{query}")],
        is_last=True,
    )


def _register_stub_tools(spec: SubAgentSpec) -> None:
    async def alpha(query: str) -> ToolResponse:
        return await _stub_search("alpha", query)

    async def beta(query: str) -> ToolResponse:
        return await _stub_search("beta", query)

    spec.tools = [alpha, beta]


async def _invoke(agent: ReActAgent, tool_call: ToolUseBlock) -> ToolResponse:
    chunk = None
    stream = await agent.toolkit.call_tool_function(tool_call)
    async for chunk in stream:
        pass
    assert chunk is not None
    return chunk


def test_search_agent_schema_and_execution() -> None:
    async def _run() -> None:
        host = build_host_agent()
        spec = build_spec("search")
        _register_stub_tools(spec)
        tool_name = await host.register_subagent(SearchAgent, spec)

        schemas = host.toolkit.get_json_schemas()
        registered = next(
            schema for schema in schemas if schema["function"]["name"] == tool_name
        )
        params = registered["function"]["parameters"]
        assert set(params.get("properties", {})) == {"query", "context"}
        assert params.get("required") == ["query"]

        await host.memory.add(
            Msg(
                name="system",
                content="Delegate all research to SearchAgent",
                role="system",
            ),
        )

        tool_call = ToolUseBlock(
            type="tool_use",
            id=f"search-{int(time())}",
            name=tool_name,
            input={
                "query": "who is speed",
                "context": "Need historical background",
            },
        )

        response = await _invoke(host, tool_call)
        metadata = response.metadata.get("response_metadata") if response.metadata else {}
        assert metadata["query"] == "who is speed"
        assert metadata["context"] == "Need historical background"
        artifact_path = metadata.get("artifact_path")
        assert artifact_path is None  # no filesystem service attached

    asyncio.run(_run())


def test_search_query_defaults() -> None:
    payload = SearchQuery(query="test topic")
    assert payload.context is None
