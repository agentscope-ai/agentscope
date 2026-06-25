# -*- coding: utf-8 -*-
"""Example of using ValkeyStore in AgentScope RAG system.

Requires a Valkey server with the Search module on localhost:6379.
"""
import asyncio

from agentscope.rag import (
    ValkeyStore,
    Document,
    DocMetadata,
)
from agentscope.message import TextBlock


async def example_basic_operations() -> None:
    """The example of basic CRUD operations with ValkeyStore."""
    print("\n" + "=" * 60)
    print("Test 1: Basic CRUD Operations")
    print("=" * 60)

    store = ValkeyStore(
        host="localhost",
        port=6379,
        index_name="example_basic_idx",
        prefix="example:basic:",
        dimensions=4,
        distance="COSINE",
    )

    print("✓ ValkeyStore initialized")

    # Create test documents with embeddings
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

    # Add documents (automatically creates index if needed)
    await store.add(test_docs)
    print(f"✓ Added {len(test_docs)} documents to the store")

    # Wait briefly for indexing
    await asyncio.sleep(0.5)

    # Search for similar documents
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

    # Search with score threshold
    results_filtered = await store.search(
        query_embedding=query_embedding,
        limit=5,
        score_threshold=0.99,
    )
    print(
        f"\n✓ Search with threshold (>0.99): "
        f"{len(results_filtered)} results",
    )

    # Delete by doc_id
    await store.delete(ids=["doc_2", "doc_3"])
    await asyncio.sleep(0.3)
    print("\n✓ Deleted doc_2 and doc_3")

    # Verify deletion
    results_after = await store.search(
        query_embedding=query_embedding,
        limit=5,
    )
    print(f"✓ After deletion: {len(results_after)} documents remain")

    # Cleanup
    await store.drop_index()
    await store.close()


async def example_multiple_chunks() -> None:
    """The example of documents with multiple chunks."""
    print("\n" + "=" * 60)
    print("Test 2: Documents with Multiple Chunks")
    print("=" * 60)

    store = ValkeyStore(
        host="localhost",
        port=6379,
        index_name="example_chunks_idx",
        prefix="example:chunks:",
        dimensions=4,
        distance="COSINE",
    )

    # Create a document split into multiple chunks
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

    await asyncio.sleep(0.5)

    # Search and verify chunk information
    results = await store.search(
        query_embedding=[0.2, 0.3, 0.4, 0.5],
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

    # Delete entire document (all chunks)
    await store.delete(ids=["book_1"])
    await asyncio.sleep(0.3)

    results_after = await store.search(
        query_embedding=[0.2, 0.3, 0.4, 0.5],
        limit=3,
    )
    print(f"\n✓ After deleting book_1: {len(results_after)} chunks remain")

    await store.drop_index()
    await store.close()


async def example_distance_metrics() -> None:
    """The example of different distance metrics."""
    print("\n" + "=" * 60)
    print("Test 3: Different Distance Metrics")
    print("=" * 60)

    metrics = ["COSINE", "L2", "IP"]

    for metric in metrics:
        print(f"\n--- Testing {metric} metric ---")
        store = ValkeyStore(
            host="localhost",
            port=6379,
            index_name=f"example_{metric.lower()}_idx",
            prefix=f"example:{metric.lower()}:",
            dimensions=4,
            distance=metric,
        )

        docs = [
            Document(
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text=f"Test doc for {metric}",
                    ),
                    doc_id=f"doc_{metric}",
                    chunk_id=0,
                    total_chunks=1,
                ),
                embedding=[0.1, 0.2, 0.3, 0.4],
            ),
        ]

        await store.add(docs)
        await asyncio.sleep(0.5)

        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3, 0.4],
            limit=1,
        )

        if results:
            print(f"✓ {metric}: Score = {results[0].score:.4f}")
        else:
            print(f"✗ {metric}: No results returned")

        await store.drop_index()
        await store.close()


async def main() -> None:
    """Run all examples."""
    print("\n" + "=" * 60)
    print("ValkeyStore Example Suite")
    print("=" * 60)

    try:
        await example_basic_operations()
        await example_multiple_chunks()
        await example_distance_metrics()

        print("\n" + "=" * 60)
        print("✓ All examples completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Example failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
