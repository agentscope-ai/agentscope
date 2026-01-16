# -*- coding: utf-8 -*-
"""Example of using OceanBaseStore in AgentScope RAG system."""
import asyncio
import os

from agentscope.rag import (
    OceanBaseStore,
    Document,
    DocMetadata,
)
from agentscope.message import TextBlock


def _client_kwargs() -> dict[str, str]:
    return {
        "uri": os.getenv("OCEANBASE_URI", "127.0.0.1:2881"),
        "user": os.getenv("OCEANBASE_USER", "root"),
        "password": os.getenv("OCEANBASE_PASSWORD", ""),
        "db_name": os.getenv("OCEANBASE_DB", "test"),
    }


def _create_store(
    collection_name: str,
    dimensions: int = 4,
    distance: str = "COSINE",
) -> OceanBaseStore:
    return OceanBaseStore(
        collection_name=collection_name,
        dimensions=dimensions,
        distance=distance,
        client_kwargs=_client_kwargs(),
    )


async def example_basic_operations() -> None:
    """The example of basic CRUD operations with OceanBaseStore."""
    print("\n" + "=" * 60)
    print("Test 1: Basic CRUD Operations")
    print("=" * 60)

    store = _create_store(collection_name="ob_basic_collection")
    store.get_client().drop_collection("ob_basic_collection")

    print("✓ OceanBaseStore initialized")

    test_docs = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Artificial Intelligence is the future",
                ),
                doc_id="doc_1",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Machine Learning is a subset of AI",
                ),
                doc_id="doc_2",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.2, 0.3, 0.4, 0.5],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Deep Learning uses neural networks",
                ),
                doc_id="doc_3",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
    ]

    await store.add(test_docs)
    print(f"✓ Added {len(test_docs)} documents to the store")

    query_embedding = [0.15, 0.25, 0.35, 0.45]
    results = await store.search(
        query_embedding=query_embedding,
        limit=2,
    )

    print(f"\n✓ Search completed, found {len(results)} results:")
    for i, result in enumerate(results, 1):
        print(f"  {i}. Score: {result.score:.4f}")
        print(f"     Content: {result.metadata.content}")
        print(f"     Doc ID: {result.metadata.doc_id}")

    results_filtered = await store.search(
        query_embedding=query_embedding,
        limit=5,
        score_threshold=0.9,
    )
    print(f"\n✓ Search with threshold (>0.9): {len(results_filtered)} results")

    client = store.get_client()
    table = client.load_table(collection_name="ob_basic_collection")
    await store.delete(where=[table.c["doc_id"] == "doc_2"])
    print("\n✓ Deleted document with doc_id='doc_2'")

    results_after_delete = await store.search(
        query_embedding=query_embedding,
        limit=5,
    )
    print(f"✓ After deletion: {len(results_after_delete)} documents remain")

    print(f"\n✓ Got MilvusLikeClient: {type(client).__name__}")


async def example_filter_search() -> None:
    """The example of search with metadata filtering."""
    print("\n" + "=" * 60)
    print("Test 2: Search with Metadata Filtering")
    print("=" * 60)

    store = _create_store(collection_name="ob_filter_collection")
    client = store.get_client()
    client.drop_collection("ob_filter_collection")

    docs = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Python is a programming language",
                ),
                doc_id="prog_1",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Java is used for enterprise applications",
                ),
                doc_id="prog_2",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.2, 0.3, 0.4, 0.5],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Neural networks are used in AI",
                ),
                doc_id="ai_1",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Deep learning requires GPUs",
                ),
                doc_id="ai_2",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.4, 0.5, 0.6, 0.7],
        ),
    ]

    await store.add(docs)
    print(f"✓ Added {len(docs)} documents with different doc_id prefixes")

    query_embedding = [0.25, 0.35, 0.45, 0.55]
    all_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
    )
    print(f"\n✓ Search without filter: {len(all_results)} results")
    for i, result in enumerate(all_results, 1):
        print(
            f"  {i}. Doc ID: {result.metadata.doc_id}, "
            f"Score: {result.score:.4f}",
        )

    table = client.load_table(collection_name="ob_filter_collection")
    prog_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
        flter=[table.c["doc_id"].like("prog%")],
    )
    print("\n✓ Search with filter (doc_id like 'prog%'):")
    for i, result in enumerate(prog_results, 1):
        print(
            f"  {i}. Doc ID: {result.metadata.doc_id}, "
            f"Score: {result.score:.4f}",
        )

    ai_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
        flter=[table.c["doc_id"].like("ai%")],
    )
    print("\n✓ Search with filter (doc_id like 'ai%'):")
    for i, result in enumerate(ai_results, 1):
        print(
            f"  {i}. Doc ID: {result.metadata.doc_id}, "
            f"Score: {result.score:.4f}",
        )


async def example_multiple_chunks() -> None:
    """The example of documents with multiple chunks."""
    print("\n" + "=" * 60)
    print("Test 3: Documents with Multiple Chunks")
    print("=" * 60)

    store = _create_store(collection_name="ob_chunks_collection")
    store.get_client().drop_collection("ob_chunks_collection")

    chunks = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Chapter 1: Introduction to AI",
                ),
                doc_id="book_1",
                chunk_id=0,
                total_chunks=3,
            ),
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Chapter 2: Machine Learning Basics",
                ),
                doc_id="book_1",
                chunk_id=1,
                total_chunks=3,
            ),
            embedding=[0.2, 0.3, 0.4, 0.5],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
                    type="text",
                    text="Chapter 3: Deep Learning Advanced",
                ),
                doc_id="book_1",
                chunk_id=2,
                total_chunks=3,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
    ]

    await store.add(chunks)
    print(f"✓ Added document with {len(chunks)} chunks")

    query_embedding = [0.2, 0.3, 0.4, 0.5]
    results = await store.search(
        query_embedding=query_embedding,
        limit=3,
    )

    print("\n✓ Search results for multi-chunk document:")
    for i, result in enumerate(results, 1):
        chunk_info = (
            f"{result.metadata.chunk_id}/{result.metadata.total_chunks}"
        )
        print(f"  {i}. Chunk {chunk_info}")
        print(f"     Content: {result.metadata.content}")
        print(f"     Score: {result.score:.4f}")


async def example_distance_metrics() -> None:
    """The example of different distance metrics."""
    print("\n" + "=" * 60)
    print("Test 4: Different Distance Metrics")
    print("=" * 60)

    metrics = ["COSINE", "L2", "IP"]

    for metric in metrics:
        print(f"\n--- Testing {metric} metric ---")
        collection_name = f"ob_{metric}_collection"
        store = _create_store(
            collection_name=collection_name,
            distance=metric,
        )
        store.get_client().drop_collection(collection_name)

        docs = [
            Document(
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text=f"Test doc for {metric}",
                    ),
                    doc_id=f"doc_{metric}_1",
                    chunk_id=0,
                    total_chunks=1,
                ),
                embedding=[0.1, 0.2, 0.3, 0.4],
            ),
        ]

        await store.add(docs)
        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3, 0.4],
            limit=1,
        )

        print(f"✓ {metric} metric: Score = {results[0].score:.4f}")


async def main() -> None:
    """Run all example."""
    print("\n" + "=" * 60)
    print("OceanBaseStore Comprehensive Test Suite")
    print("=" * 60)

    try:
        await example_basic_operations()
        await example_filter_search()
        await example_multiple_chunks()
        await example_distance_metrics()

        print("\n" + "=" * 60)
        print("✓ All tests completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
