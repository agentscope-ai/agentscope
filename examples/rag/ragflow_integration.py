# -*- coding: utf-8 -*-
"""Integrate RAGFlow with AgentScope as a retrieval tool (Path 2).

RAGFlow is a full-featured RAG engine (parsing, chunking, hybrid
retrieval, reranking). Instead of shoehorning it into AgentScope's
``VectorStoreBase`` abstraction, we expose its retrieval REST API as a
:class:`~agentscope.tool.FunctionTool`. The agent decides when to call
it -- same agentic pattern as ``RAGMiddleware``'s ``"agentic"`` mode,
but backed by RAGFlow.

Run with::

    RAGFLOW_API_KEY=ragflow-xxx \
    DASHSCOPE_API_KEY=sk-... \
    python examples/rag/ragflow_integration.py
"""
import os
from typing import Any

import httpx

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.tool import FunctionTool, Toolkit


# ---------------------------------------------------------------------------
# 1. RAGFlow retrieval client + tool function
# ---------------------------------------------------------------------------

def _make_ragflow_search_tool(
    base_url: str,
    api_key: str,
    dataset_ids: list[str],
    top_k: int = 5,
    similarity_threshold: float = 0.2,
    timeout: float = 30.0,
) -> FunctionTool:
    """Build a :class:`FunctionTool` that calls the RAGFlow retrieval API.

    Configuration is captured in a closure so the tool function exposed
    to the LLM only has one parameter -- the question. This keeps the
    JSON schema clean and prevents the model from guessing internal IDs.

    Args:
        base_url (`str`):
            RAGFlow server base URL, e.g. ``http://localhost:9380``.
        api_key (`str`):
            RAGFlow API key (from "User Settings -> API Key").
        dataset_ids (`list[str]`):
            The RAGFlow dataset(s) to search across.
        top_k (`int`, optional):
            Max chunks to return. Defaults to ``5``.
        similarity_threshold (`float`, optional):
            Minimum similarity score, 0.0 - 1.0. Defaults to ``0.2``.
        timeout (`float`, optional):
            HTTP request timeout in seconds. Defaults to ``30.0``.

    Returns:
        `FunctionTool`: A tool ready to register in a :class:`Toolkit`.
    """
    # One shared async client per tool instance -- connection pooling.
    client = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    async def search_ragflow(question: str) -> dict[str, Any]:
        """Search the knowledge base for relevant context.

        Call this when you need to look up facts, policies, or any
        information from the documents. Always prefer this tool over
        guessing. The returned chunks are pre-ranked by the RAGFlow
        retrieval engine.

        Args:
            question: A natural-language query describing what you want
                to find. Use the most specific terms possible.

        Returns:
            A dict with a "chunks" list; each chunk has "content",
            "document_name", and "similarity". Empty list if nothing
            matched.
        """
        payload = {
            "question": question,
            "dataset_ids": dataset_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
        }
        resp = await client.post("/api/v1/retrieval", json=payload)
        resp.raise_for_status()
        body = resp.json()

        # RAGFlow returns {"code": 0, "data": {"chunks": [...]}}
        if body.get("code") != 0:
            return {
                "chunks": [],
                "error": body.get("message", "RAGFlow retrieval failed"),
            }

        chunks = [
            {
                "content": c.get("content", ""),
                "document_name": c.get("document_keyword")
                or c.get("document_name")
                or c.get("doc_name", ""),
                "similarity": round(c.get("similarity", 0.0), 4),
            }
            for c in body.get("data", {}).get("chunks", [])
        ]
        return {"chunks": chunks}

    return FunctionTool(
        func=search_ragflow,
        name="search_knowledge",
        is_concurrency_safe=True,
        is_read_only=True,
    )


# ---------------------------------------------------------------------------
# 2. Build the agent and run a conversation
# ---------------------------------------------------------------------------

def build_agent(
    chat_model: DeepSeekChatModel,
    ragflow_tool: FunctionTool,
) -> Agent:
    """Construct an :class:`Agent` with the RAGFlow retrieval tool attached."""
    return Agent(
        name="ragflow-agent",
        system_prompt=(
            "You are a knowledgeable assistant. When the user asks about "
            "documented facts, policies, or any content you are not certain "
            "about, ALWAYS call the `search_knowledge` tool first and base "
            "your answer on the returned chunks. Cite the document name when "
            "you quote a chunk. If the tool returns nothing relevant, say so "
            "honestly."
        ),
        model=chat_model,
        toolkit=Toolkit(tools=[ragflow_tool]),
    )


async def ask(agent: Agent, question: str) -> None:
    """Run one reply and print it."""
    print(f"\n[user] {question}")
    reply = await agent.reply(UserMsg(name="user", content=question))
    print(f"[assistant] {reply.get_text_content()}")


async def main() -> None:
    """The main entry point of the example."""
    ragflow_key = os.environ.get("RAGFLOW_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not ragflow_key or not deepseek_key:
        raise RuntimeError(
            "Set RAGFLOW_API_KEY and DEEPSEEK_API_KEY before running "
            "this example.",
        )

    # --- Build the RAGFlow tool ---
    ragflow_tool = _make_ragflow_search_tool(
        base_url=os.environ.get("RAGFLOW_BASE_URL", "http://localhost:9380"),
        api_key=ragflow_key,
        dataset_ids=[os.environ.get(
            "RAGFLOW_DATASET_ID",
            "your-dataset-id",
        )],
        top_k=5,
    )

    # --- Build the agent ---
    credential = DeepSeekCredential(api_key=deepseek_key)
    chat_model = DeepSeekChatModel(
        credential=credential,
        model="deepseek-chat",
        stream=False,
    )
    agent = build_agent(chat_model, ragflow_tool)

    # --- Demo conversation (dataset contains a docker operations doc) ---
    await ask(
        agent,
        "How do I deploy docker on a server? Summarise the key steps.",
    )
    await ask(
        agent,
        "What commands are used to save a docker image as a tar file "
        "and load it back later?",
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
