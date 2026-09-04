# -*- coding: utf-8 -*-
"""RAGFlow-backed knowledge — no parse/chunk/embed pipeline on your side.

:class:`~agentscope.rag.RAGFlowKnowledge` is the knowledge-layer
integration for [RAGFlow](https://ragflow.io/), a managed end-to-end RAG
pipeline.  Unlike :class:`~agentscope.rag.KnowledgeBase`, you do **not**
bring your own parser, chunker, embedding model, or vector store:
RAGFlow parses, chunks, indexes, and retrieves on the server side using
the model and parsing strategy configured on its dataset.

This example walks through the knowledge operations — :meth:`insert_document`,
:meth:`search`, :meth:`list_documents`, :meth:`delete_document` — against a
RAGFlow dataset you have already created in the RAGFlow console.  Note that
``insert_document`` uploads and requests server-side *indexing* asynchronously,
so the example polls the document's parse status before searching.

Run with::

    RAGFLOW_API_KEY=ragflow-xxxxx \\
    RAGFLOW_BASE_URL=http://localhost:9380 \\
    RAGFLOW_DATASET_ID=kb-xxxxx \\
    python examples/rag/ragflow_knowledge.py
"""
import asyncio
import os

from agentscope.message import TextBlock
from agentscope.rag import RAGFlowConfig, RAGFlowKnowledge


# RAGFlow document parse states that mean "finished" (or can never finish).
_TERMINAL_RUN = {"DONE", "FAIL", "CANCEL"}


async def wait_until_indexed(
    knowledge: RAGFlowKnowledge,
    document_id: str,
    timeout_sec: float = 120.0,
    poll_interval_sec: float = 3.0,
) -> None:
    """Poll a just-uploaded RAGFlow document until RAGFlow has parsed it.

    RAGFlow indexing is asynchronous: ``insert_document`` returns as soon as
    the upload is accepted.  ``search`` only sees a document once RAGFlow has
    finished parsing/chunking it, so callers that want to search right away
    should wait for the parse to complete.

    Args:
        knowledge (`RAGFlowKnowledge`): The handle used to poll.
        document_id (`str`): The document to wait on.
        timeout_sec (`float`): Give up after this many seconds.
        poll_interval_sec (`float`): Seconds between polls.
    """
    elapsed = 0.0
    while elapsed < timeout_sec:
        for summary in await knowledge.list_documents():
            if summary.document_id != document_id:
                continue
            run = summary.metadata.get("run", "")
            if str(run).upper() in _TERMINAL_RUN:
                return
            # ``progress`` reaches 1.0 (100%) on completion.
            progress = summary.metadata.get("parse_progress", 0.0)
            if float(progress) >= 1.0:
                return
        await asyncio.sleep(poll_interval_sec)
        elapsed += poll_interval_sec
    print(
        f"  WARNING: document {document_id!r} not indexed within "
        f"{timeout_sec}s; search results may be incomplete.",
    )


async def search_and_print(
    knowledge: RAGFlowKnowledge,
    query: str,
    top_k: int = 3,
) -> None:
    """Run a search via the :class:`RAGFlowKnowledge` handle and print hits."""
    results = await knowledge.search([query], top_k=top_k)

    print(f"\nQuery: {query!r}")
    if not results:
        print("  (no hits)")
        return
    for rank, result in enumerate(results, start=1):
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
    api_key = os.environ.get("RAGFLOW_API_KEY")
    base_url = os.environ.get("RAGFLOW_BASE_URL", "http://localhost:9380")
    dataset_id = os.environ.get("RAGFLOW_DATASET_ID")
    if not api_key or not dataset_id:
        raise RuntimeError(
            "Set RAGFLOW_API_KEY and RAGFLOW_DATASET_ID before running "
            "this example (RAGFLOW_BASE_URL defaults to "
            "http://localhost:9380).",
        )

    knowledge = RAGFlowKnowledge(
        name="demo-kb",
        description="A toy RAGFlow corpus on cats.",
        config=RAGFlowConfig(
            api_key=api_key,
            base_url=base_url,
            dataset_id=dataset_id,
            top_k=10,
            similarity_threshold=0.2,
        ),
    )

    # RAGFlow parses/chunks/indexes the uploaded bytes on the server,
    # *asynchronously*.  The returned document id is yours to keep for
    # delete_document.
    document_id = await knowledge.insert_document(
        b"# Cats\n\nCats are small carnivorous mammals. They are popular "
        b"as pets for their playful and affectionate nature.\n",
        filename="cats.md",
    )
    print(f"Uploaded document_id={document_id}; waiting for indexing ...")
    await wait_until_indexed(knowledge, document_id)

    await search_and_print(knowledge, "What are cats known for?")

    print("\nDocuments in the dataset:")
    for summary in await knowledge.list_documents():
        print(
            f"  - {summary.source!r} "
            f"(id={summary.document_id}, chunks={summary.chunk_count})",
        )

    # Uncomment to remove the uploaded document.
    # await knowledge.delete_document(document_id)


if __name__ == "__main__":
    asyncio.run(main())
