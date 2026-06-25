# -*- coding: utf-8 -*-
"""Library-mode RAG walk-through — no FastAPI service, no manager.

End-to-end demo of the four building blocks in :mod:`agentscope.rag`:

1. **Vector store** — ``QdrantStore`` (in-memory here; swap to
   ``url=...`` for a real Qdrant server).
2. **Parser** — ``TextParser`` turning raw bytes into ``Section`` objects.
3. **Chunker** — ``ApproxTokenChunker`` splitting sections into ``Chunk``
   objects (the indexable unit).
4. **Embedding** — any ``EmbeddingModelBase`` subclass; we use the
   DashScope text-embedding model.

The pipeline:

    bytes ── parser ──► Section[] ── chunker ──► Chunk[]
                                                  │
                                                  ▼
                                       embedding_model(chunk.text)
                                                  │
                                                  ▼
                               VectorRecord(vector, document_id, chunk)
                                                  │
                                                  ▼
                                       vector_store.insert(...)

Retrieval mirrors the same shape: embed the query, hand the vector to
``vector_store.search``, get ``VectorSearchResult`` back.

Run with::

    DASHSCOPE_API_KEY=sk-... python examples/rag/main.py
"""
import asyncio
import os
import uuid

from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.message import TextBlock
from agentscope.rag import (
    ApproxTokenChunker,
    QdrantStore,
    TextParser,
    VectorRecord,
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
    store: QdrantStore,
    parser: TextParser,
    chunker: ApproxTokenChunker,
    embedding_model: DashScopeEmbeddingModel,
) -> None:
    """Parse → chunk → embed → insert for every demo document."""
    for filename, file_bytes in DOCUMENTS.items():
        # 1. Parse: bytes → list[Section]
        sections = await parser.parse(file=file_bytes, filename=filename)

        # 2. Chunk: list[Section] → list[Chunk]
        chunks = await chunker.chunk(sections)

        # 3. Embed: text chunks → list[list[float]].
        #    The embedding model accepts ``str | DataBlock``, so for
        #    text chunks we pass the underlying string. (Multimodal
        #    chunks would pass the ``DataBlock`` directly.)
        inputs = [
            chunk.content.text
            if isinstance(chunk.content, TextBlock)
            else chunk.content
            for chunk in chunks
        ]
        response = await embedding_model(inputs)

        # 4. Wrap each (vector, chunk) pair with the document id and
        #    insert. ``document_id`` is yours to choose — anything that
        #    uniquely names this source document inside the collection.
        document_id = uuid.uuid4().hex
        records = [
            VectorRecord(
                vector=vector,
                document_id=document_id,
                chunk=chunk,
            )
            for vector, chunk in zip(response.embeddings, chunks)
        ]
        await store.insert(COLLECTION, records)
        print(
            f"  indexed {filename!r} as document_id={document_id} "
            f"({len(chunks)} chunk(s))",
        )


async def search(
    store: QdrantStore,
    embedding_model: DashScopeEmbeddingModel,
    query: str,
    top_k: int = 3,
) -> None:
    """Embed the query, search, print the hits."""
    response = await embedding_model([query])
    results = await store.search(
        collection=COLLECTION,
        query_vector=response.embeddings[0],
        top_k=top_k,
    )

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
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set DASHSCOPE_API_KEY before running this example.",
        )

    # The four building blocks. All of these are also what the
    # service-mode (``create_app``) wiring uses internally — the only
    # difference is that here you drive them yourself.
    embedding_model = DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key=api_key),
        model="text-embedding-v4",
        parameters=DashScopeEmbeddingModel.Parameters(),
        dimensions=1024,
    )
    parser = TextParser()
    chunker = ApproxTokenChunker(chunk_size=256, overlap=32)
    store = QdrantStore(location=":memory:")

    # ``QdrantStore`` is an async context manager — entering it opens
    # the client connection; exiting closes it.
    async with store:
        await store.create_collection(COLLECTION, dimensions=1024)

        print("Indexing demo corpus ...")
        await build_index(store, parser, chunker, embedding_model)

        # A couple of retrieval queries that demonstrate scoring.
        await search(store, embedding_model, "When are cats most active?")
        await search(
            store,
            embedding_model,
            "What framework lets me build multi-agent apps?",
        )


if __name__ == "__main__":
    asyncio.run(main())
