# -*- coding: utf-8 -*-
"""Wire :class:`RAGMiddleware` into an :class:`Agent` — library mode.

The middleware in :mod:`agentscope.middleware._rag` is the agent-side
half of RAG: given one or more :class:`~agentscope.rag.KnowledgeBase`
handles (each pairing an embedding model with a vector-store
collection), the middleware drives search on each new user turn and
feeds the matched chunks into the model context.

This example reuses the indexing pipeline shown in ``index_and_search.py``
(parse → chunk → embed → insert) and then attaches the same knowledge base
to two agents — one per mode:

- ``"static"``: on the first reasoning step of a reply, embed the
  user's question, search, and inject the top hits as a one-shot
  :class:`HintBlock` into the agent's context. The model sees the
  matched snippets but never "decides" to search.
- ``"agentic"`` (the default): expose a ``search_knowledge`` tool. The
  model decides when (and what) to search, the same way it decides any
  other tool call.

Run with::

    DASHSCOPE_API_KEY=sk-... python examples/rag/integrate_with_agent.py
"""
import asyncio
import os

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.message import UserMsg
from agentscope.middleware import RAGMiddleware
from agentscope.model import DashScopeChatModel
from agentscope.rag import (
    ApproxTokenChunker,
    KnowledgeBase,
    QdrantStore,
    TextParser,
)
from agentscope.tool import Toolkit


COLLECTION = "demo-kb"

KNOWLEDGE: dict[str, bytes] = {
    "company-policy.md": (
        b"# Acme Remote Work Policy\n\n"
        b"Employees may work remotely up to three days per week. "
        b"Wednesdays are mandatory in-office days for the whole "
        b"engineering org so cross-team syncs land on a predictable "
        b"day.\n\n"
        b"Equipment stipend: each new hire receives a USD 1,500 "
        b"one-off stipend for a home-office setup. Receipts must be "
        b"submitted within 90 days of the start date.\n"
    ),
    "release-notes.md": (
        b"# AgentScope 3.0 release notes\n\n"
        b"- New ``agentscope.rag`` module: pluggable parser, chunker, "
        b"embedding, and vector-store backends.\n"
        b"- ``RAGMiddleware`` ships in two modes -- ``static`` for "
        b"automatic injection, ``agentic`` for tool-driven search.\n"
        b"- Knowledge base service supports embedded and dedicated "
        b"worker deployments through a single message-bus channel.\n"
    ),
}


async def index_corpus(knowledge: KnowledgeBase) -> None:
    """Index the demo corpus into the knowledge base.

    Identical pipeline to ``examples/rag/index_and_search.py`` — extracted
    as a helper here so the agent-side wiring stays the focus.  Each source
    file becomes one logical document; ``KnowledgeBase.insert_document``
    embeds and inserts every chunk in a single batch.
    """
    parser = TextParser()
    chunker = ApproxTokenChunker(chunk_size=256, overlap=32)
    for filename, file_bytes in KNOWLEDGE.items():
        sections = await parser.parse(file=file_bytes, filename=filename)
        chunks = await chunker.chunk(sections)
        await knowledge.insert_document(
            chunks,
            document_metadata={"filename": filename},
        )


def build_agent(
    name: str,
    *,
    chat_model: DashScopeChatModel,
    rag_mw: RAGMiddleware,
) -> Agent:
    """Construct an :class:`Agent` with the RAG middleware attached.

    The middleware is just one entry in the ``middlewares=`` list; it
    composes with every other middleware (tool offload, mem0, ...) the
    agent uses.
    """
    return Agent(
        name=name,
        system_prompt=(
            "You are a concise assistant. Use matched context when "
            "available; if you don't know, say so."
        ),
        model=chat_model,
        toolkit=Toolkit(),
        middlewares=[rag_mw],
    )


async def ask(agent: Agent, question: str) -> None:
    """Run one reply and print the final assistant message."""
    print(f"\n[{agent.name}] user: {question}")
    reply = await agent.reply(UserMsg(name="user", content=question))
    print(f"[{agent.name}] assistant: {reply.get_text_content()}")


async def main() -> None:
    """The main entry point of the example."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DASHSCOPE_API_KEY before running this example.",
        )

    credential = DashScopeCredential(api_key=api_key)
    chat_model = DashScopeChatModel(
        credential=credential,
        model="qwen-plus",
        stream=False,
    )
    embedding_model = DashScopeEmbeddingModel(
        credential=credential,
        model="text-embedding-v4",
        dimensions=1024,
    )

    store = QdrantStore(location=":memory:")
    async with store:
        # One :class:`KnowledgeBase` handle binds embedding + vector store +
        # collection together.  ``insert_document`` / ``search`` /
        # ``list_documents`` all go through it, and the backing
        # collection is created lazily on first use.
        knowledge = KnowledgeBase(
            name="acme-handbook",
            description="Acme HR policies and AgentScope 3.0 release notes.",
            embedding_model=embedding_model,
            vector_store=store,
            collection=COLLECTION,
        )

        await index_corpus(knowledge)

        # ---- Mode 1: static ----
        # Search is automatic on the first reasoning step. The injected
        # ``HintBlock`` is one-shot (removed after the model call) so it
        # doesn't poison the next turn.
        static_mw = RAGMiddleware(
            knowledge_bases=[knowledge],
            parameters=RAGMiddleware.Parameters(
                mode="static",
                top_k=3,
                emit_hint_event=False,
            ),
        )
        static_agent = build_agent(
            "rag-static-agent",
            chat_model=chat_model,
            rag_mw=static_mw,
        )
        await ask(
            static_agent,
            "How many remote days per week does Acme allow?",
        )

        # ---- Mode 2: agentic ----
        # The middleware exposes a ``search_knowledge`` tool instead of
        # auto-injecting. The model decides when to call it; it may
        # also pass ``knowledge_bases=[...]`` to scope the search when
        # multiple knowledge bases are bound.
        agentic_mw = RAGMiddleware(
            knowledge_bases=[knowledge],
            parameters=RAGMiddleware.Parameters(mode="agentic", top_k=3),
        )
        agentic_agent = build_agent(
            "rag-agentic-agent",
            chat_model=chat_model,
            rag_mw=agentic_mw,
        )
        await ask(
            agentic_agent,
            "Summarise what's new in the AgentScope 3.0 release notes.",
        )


if __name__ == "__main__":
    asyncio.run(main())
