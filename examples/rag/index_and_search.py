# -*- coding: utf-8 -*-
"""Library-mode RAG walk-through — no FastAPI service, no manager.

End-to-end demo of the building blocks in :mod:`agentscope.rag`:

1. **Vector store** — :class:`QdrantStore` (in-memory here; swap to
   ``url=...`` for a real Qdrant server).
2. **Parser** — :class:`TextParser` turning raw bytes into ``Section``
   objects.
3. **Chunker** — :class:`ApproxTokenChunker` splitting sections into
   ``Chunk`` objects (the indexable unit).
4. **Embedding** — any :class:`EmbeddingModelBase` subclass; we use the
   DashScope text-embedding model.
5. **KnowledgeBase** — the runtime handle that ties (embedding, vector
   store, collection) together and exposes
   ``insert_document`` / ``search`` / ``list_documents`` /
   ``delete_document``.  This is what :class:`RAGMiddleware` consumes.

The pipeline:

    bytes ── parser ──► Section[] ── chunker ──► Chunk[]
                                                  │
                                                  ▼
                                   knowledge.insert_document(chunks)
                                                  │
                                                  ▼
                            embed → store on the bound collection

Search mirrors the same shape: hand the query to
``knowledge.search(...)``, get ``VectorSearchResult`` back.

Run with::

    DASHSCOPE_API_KEY=sk-... python examples/rag/index_and_search.py
"""
import asyncio
import os

from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.message import TextBlock
from agentscope.rag import (
    ApproxTokenChunker,
    KnowledgeBase,
    QdrantStore,
    TextParser,
)


COLLECTION = "demo-kb"

# A toy corpus inlined as bytes so the example has no on-disk
# dependencies. In real use these would come from uploaded files or
# blob-store reads.
DOCUMENTS: dict[str, bytes] = {
    "cats.md": (
        b"# Cats\n\n"
        b"Cats are small carnivorous mammals. They are popular as pets "
        b"because of their playful and affectionate nature.\n\n"
        b"Domestic cats sleep around 12-16 hours per day. They are most "
        b"active at dawn and dusk (crepuscular behaviour).\n"
    ),
    "agentscope.md": (
        b"# AgentScope\n\n"
        b"AgentScope is a developer-centric framework for building "
        b"multi-agent LLM applications. It emphasises transparency, "
        b"controllability, and a clear separation between agent logic "
        b"and infrastructure.\n\n"
        b"Its RAG module ships a parser/chunker/embedding/vector-store "
        b"pipeline that can be wired up without the FastAPI service.\n"
    ),
}


async def build_index(
    knowledge: KnowledgeBase,
    parser: TextParser,
    chunker: ApproxTokenChunker,
) -> None:
    """Parse → chunk → insert for every demo document.

    Embedding and vector-store insertion are encapsulated inside
    ``knowledge.insert_document`` — caller side only has to bring
    pre-chunked content.
    """
    for filename, file_bytes in DOCUMENTS.items():
        # 1. Parse: bytes → list[Section]
        sections = await parser.parse(file=file_bytes, filename=filename)

        # 2. Chunk: list[Section] → list[Chunk]
        chunks = await chunker.chunk(sections)

        # 3. Insert: embeds every chunk under one document id.  The
        #    returned id is yours to keep for later
        #    ``delete_document``.
        document_id = await knowledge.insert_document(
            chunks,
            document_metadata={"filename": filename},
        )
        print(
            f"  indexed {filename!r} as document_id={document_id} "
            f"({len(chunks)} chunk(s))",
        )


async def search(
    knowledge: KnowledgeBase,
    query: str,
    top_k: int = 3,
) -> None:
    """Run a search via the :class:`KnowledgeBase` handle and print hits."""
    results = await knowledge.search(queries=[query], top_k=top_k)

    print(f"\nQuery: {query!r}")
    if not results:
        print("  (no hits)")
        return
    for rank, result in enumerate(results, start=1):
        # Only text chunks are printable as-is.
        snippet = (
            result.chunk.content.text
            if isinstance(result.chunk.content, TextBlock)
            else "<non-text chunk>"
        )
        snippet = snippet.replace("\n", " ").strip()
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        print(
            f"  [{rank}] score={result.score:.4f} "
            f"source={result.chunk.source} "
            f"document_id={result.document_id}\n"
            f"      {snippet}",
        )


async def main() -> None:
    """The main entry point of the example."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DASHSCOPE_API_KEY before running this example.",
        )

    # The building blocks. All of these are also what the service-mode
    # (``create_app``) wiring uses internally — the only difference is
    # that here you drive them yourself.
    embedding_model = DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key=api_key),
        model="text-embedding-v4",
        dimensions=1024,
    )
    parser = TextParser()
    chunker = ApproxTokenChunker(chunk_size=256, overlap=32)
    store = QdrantStore(location=":memory:")

    # ``QdrantStore`` is an async context manager — entering it opens
    # the client connection; exiting closes it.
    async with store:
        # One :class:`KnowledgeBase` handle bundles (embedding, vector
        # store, collection) together so the rest of this example
        # never has to repeat the wiring.  The collection is created
        # lazily on the first operation (`build_index`).
        knowledge = KnowledgeBase(
            name="demo-kb",
            description="A toy corpus on cats and AgentScope.",
            embedding_model=embedding_model,
            vector_store=store,
            collection=COLLECTION,
        )

        print("Indexing demo corpus ...")
        await build_index(knowledge, parser, chunker)

        # A couple of search queries that demonstrate scoring.
        await search(knowledge, "When are cats most active?")
        await search(
            knowledge,
            "What framework lets me build multi-agent apps?",
        )


if __name__ == "__main__":
    asyncio.run(main())
