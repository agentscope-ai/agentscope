# -*- coding: utf-8 -*-
"""Example: ExternalRetrievalMiddleware with RAGFlow backend.

This example shows how to use the official
:class:`~agentscope.middleware.ExternalRetrievalMiddleware` with a
RAGFlow backend for automatic knowledge-base injection.  The middleware
intercepts the first reasoning step, calls RAGFlow's retrieval API, and
injects matched chunks as a one-shot hint — the LLM never decides
"should I search".

Run with::

    RAGFLOW_API_KEY=ragflow-xxx \
    DEEPSEEK_API_KEY=sk-... \
    python examples/rag/ragflow_middleware_integration.py
"""
import asyncio
import os

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.middleware import (
    ExternalRetrievalMiddleware,
    RAGFlowRetrievalBackend,
)
from agentscope.model import DeepSeekChatModel


# ---------------------------------------------------------------------------
# Build the agent and run a conversation
# ---------------------------------------------------------------------------

def build_agent(
    chat_model: DeepSeekChatModel,
    middleware: ExternalRetrievalMiddleware,
) -> Agent:
    """Construct an Agent with RAGFlow auto-injection."""
    return Agent(
        name="ragflow-middleware-agent",
        system_prompt=(
            "You are a knowledgeable assistant. Answer based on the "
            "retrieved context when available. Cite the document name "
            "when you quote a chunk. If no relevant context was "
            "retrieved, say so honestly."
        ),
        model=chat_model,
        middlewares=[middleware],
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

    # --- Build the RAGFlow backend + middleware ---
    backend = RAGFlowRetrievalBackend(
        base_url=os.environ.get("RAGFLOW_BASE_URL", "http://localhost:9380"),
        api_key=ragflow_key,
        dataset_ids=[os.environ.get(
            "RAGFLOW_DATASET_ID",
            "your-dataset-id",
        )],
    )
    middleware = ExternalRetrievalMiddleware(
        backend=backend,
        top_k=5,
        similarity_threshold=0.2,
    )

    # --- Build the agent ---
    credential = DeepSeekCredential(api_key=deepseek_key)
    chat_model = DeepSeekChatModel(
        credential=credential,
        model="deepseek-chat",
        stream=False,
    )
    agent = build_agent(chat_model, middleware)

    try:
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
        await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
