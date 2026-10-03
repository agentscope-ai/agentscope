# -*- coding: utf-8 -*-
"""Integrate RAGFlow with AgentScope via MCP (SSE transport).

Companion to ``ragflow_integration.py`` — same agentic retrieval goal,
but instead of hand-wrapping RAGFlow's REST API as a ``FunctionTool``
(~90 lines of HTTP payload / response parsing / schema authoring),
we connect to RAGFlow's MCP server over SSE and let AgentScope's
``MCPClient`` discover the retrieval tools automatically.

What disappears vs. the REST version:
- No ``httpx.AsyncClient`` boilerplate.
- No manual request/response JSON handling.
- No hand-written ``FunctionTool`` docstring / JSON schema — the tool
  name, description, and parameter schema all come from the RAGFlow
  MCP server.

What stays the same:
- The agent still decides when to search (agentic pattern).
- The system prompt still nudges it to cite sources.

Run with::

    RAGFLOW_API_KEY=ragflow-xxx \
    DEEPSEEK_API_KEY=sk-... \
    python examples/rag/ragflow_mcp_integration.py

Optionally override the MCP endpoint::

    RAGFLOW_MCP_URL=http://your-host:port/sse \
    RAGFLOW_API_KEY=ragflow-xxx \
    DEEPSEEK_API_KEY=sk-... \
    python examples/rag/ragflow_mcp_integration.py
"""
import asyncio
import os

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.mcp import MCPClient, HttpMCPConfig
from agentscope.permission import (
    PermissionBehavior,
    PermissionRule,
)
from agentscope.tool import Toolkit


RAGFLOW_MCP_URL = os.environ.get(
    "RAGFLOW_MCP_URL",
    "http://localhost:9382/sse",
)


def build_agent(
    chat_model: DeepSeekChatModel,
    mcp_client: MCPClient,
) -> Agent:
    """Construct an Agent with RAGFlow MCP tools attached.

    ``Toolkit(mcps=[...])`` pulls every tool the MCP server exposes and
    registers them under the ``mcp__ragflow__<tool>`` namespace. Use
    ``enable_tools`` / ``disable_tools`` on ``MCPClient`` to filter if
    the server exposes more than you want the model to see.

    MCP tools default to ASK permission (they aren't auto-allowed unless
    the server sets ``readOnlyHint``). For this unattended demo we add an
    allow rule for every tool the RAGFlow MCP exposes so the agent can
    call them without interactive confirmation. In an interactive UI you
    would instead surface the ASK to the user.
    """
    return Agent(
        name="ragflow-mcp-agent",
        system_prompt=(
            "You are a knowledgeable assistant. When the user asks about "
            "documented facts, policies, or any content you are not certain "
            "about, ALWAYS call the search/retrieval tool first and base "
            "your answer on the returned chunks. Cite the document name "
            "when you quote a chunk. If the tool returns nothing relevant, "
            "say so honestly.\n\n"
            "IMPORTANT: When calling the retrieval tool, pass the user's "
            "question as the `question` parameter, and ALWAYS pass "
            'dataset_ids=["your-dataset-id"] to search '
            "the Docker operations handbook dataset. Do NOT skip the tool "
            "call."
        ),
        model=chat_model,
        toolkit=Toolkit(mcps=[mcp_client]),
    )


async def ask(agent: Agent, question: str) -> None:
    """Run one reply and print it."""
    print(f"\n[user] {question}")
    reply = await agent.reply(UserMsg(name="user", content=question))
    print(f"[assistant] {reply.get_text_content()}")


async def main() -> None:
    """The main entry point of the example."""
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    ragflow_key = os.environ.get("RAGFLOW_API_KEY")
    if not deepseek_key or not ragflow_key:
        raise RuntimeError(
            "Set DEEPSEEK_API_KEY and RAGFLOW_API_KEY before running "
            "this example.",
        )

    # --- MCP client (SSE transport) ---
    # The URL ends with /sse, so MCPClient auto-selects SSE transport
    # (see _mcp_client._create_http_client). Stateful mode keeps the
    # SSE connection open across tool calls instead of reconnecting
    # per invocation. The RAGFlow MCP server requires an API key
    # (Bearer token) for retrieval calls — list_tools works without
    # it, but call_tool returns "Authentication error: API key is
    # invalid!".
    mcp_client = MCPClient(
        name="ragflow",
        is_stateful=True,
        mcp_config=HttpMCPConfig(
            url=RAGFLOW_MCP_URL,
            headers={"Authorization": f"Bearer {ragflow_key}"},
            timeout=30.0,
        ),
    )

    # Stateful MCP requires explicit connect() before use — Toolkit
    # assumes stateful clients are already connected.
    await mcp_client.connect()
    try:
        # Discover what tools RAGFlow exposes via MCP (for visibility).
        tools = await mcp_client.list_tools()
        print(f"RAGFlow MCP exposes {len(tools)} tool(s):")
        for t in tools:
            desc = (t.description or "")[:80]
            print(f"  - {t.name}: {desc}")

        # --- Build the agent ---
        chat_model = DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=deepseek_key),
            model="deepseek-chat",
            stream=False,
        )

        # MCP tools default to ASK permission. Add an allow rule for
        # each discovered tool so the agent can call them without
        # interactive confirmation in this unattended demo. We mutate
        # the agent's existing permission_context (the PermissionEngine
        # holds a reference to the same object, so replacement would
        # NOT be seen by the engine).
        agent = build_agent(chat_model, mcp_client)
        for t in tools:
            agent.state.permission_context.allow_rules.setdefault(
                t.name, [],
            ).append(
                PermissionRule(
                    tool_name=t.name,
                    rule_content=None,
                    behavior=PermissionBehavior.ALLOW,
                    source="demo",
                ),
            )

        # --- Demo conversation (same questions as ragflow_integration.py) ---
        await ask(
            agent,
            "How do I deploy docker on a server? Summarise the key steps.",
        )
        await ask(
            agent,
            "What commands are used to save a docker image as a tar file "
            "and load it back later?",
        )
    finally:
        await mcp_client.close()


if __name__ == "__main__":
    asyncio.run(main())
