# -*- coding: utf-8 -*-
"""Example of using AlibabaCloudAnalyticDBStore in AgentScope RAG system."""
import asyncio
import os

from agentscope.rag import (
    AlibabaCloudAnalyticDBStore,
    Document,
    DocMetadata,
)
from agentscope.message import TextBlock


async def example_operations() -> None:
    """The example of all operations."""
    print("\n" + "=" * 60)
    print("Test 1: example_operations")
    print("=" * 60)

    store = AlibabaCloudAnalyticDBStore(
        host=os.getenv("ADB_HOST"),
        port=int(os.getenv("ADB_PORT", "3306")),
        user=os.getenv("ADB_USER"),
        password=os.getenv("ADB_PASSWORD"),
        database=os.getenv("ADB_DATABASE", "vectorstore"),
        table_name=os.getenv("ADB_TABLE", "agentscope"),
        dimensions=4,
        distance="EUCLIDEAN",
    )

    # Create documents with different categories
    docs = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Python is a programming language"),
                doc_id="prog_1",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(
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
                content=TextBlock(text="Neural networks are used in AI"),
                doc_id="ai_1",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Deep learning requires GPUs"),
                doc_id="ai_2",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.4, 0.5, 0.6, 0.7],
        ),
    ]

    await store.add(docs)
    print(f"✓ Added {len(docs)} documents with different doc_id prefixes")

    # Search
    query_embedding = [0.25, 0.35, 0.45, 0.55]
    all_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
    )
    print(f"\n✓ Search: {len(all_results)} results")
    for i, result in enumerate(all_results, 1):
        doc_id = result.metadata.doc_id
        score = result.score
        print(f"  {i}. Doc ID: {doc_id}, Score: {score:.4f}")

    # Search with score threshold
    score_results = await store.search(
        query_embedding=query_embedding,
        limit=5,
        score_threshold=0.1,
    )
    print(f"\n✓ Search with threshold (<=0.1): {len(score_results)} results")

    # Truncate table
    await store.delete()
    print("\n✓ Deleted all documents")

    # Verify deletion
    results_after_delete = await store.search(
        query_embedding=query_embedding,
        limit=5,
    )
    print(f"✓ After deletion: {len(results_after_delete)} documents remain")

    # Get client for advanced operations
    client = store.get_client()
    print(f"\n✓ Got connection: {type(client).__name__}")

    # Close connection
    store.close()
    print("✓ Connection closed")


async def example_distance_metrics() -> None:
    """The example of different distance metrics."""
    print("\n" + "=" * 60)
    print("Test 2: example_distance_metrics")
    print("=" * 60)

    # Test with different metrics
    metrics = ["EUCLIDEAN", "COSINE"]

    for metric in metrics:
        print(f"\n--- Testing {metric} metric ---")
        store = AlibabaCloudAnalyticDBStore(
            host=os.getenv("ADB_HOST"),
            port=int(os.getenv("ADB_PORT", "3306")),
            user=os.getenv("ADB_USER"),
            password=os.getenv("ADB_PASSWORD"),
            database=os.getenv("ADB_DATABASE", "vectorstore"),
            table_name=f"{metric.lower()}_vectors",
            dimensions=4,
            distance=metric,
        )

        docs = [
            Document(
                metadata=DocMetadata(
                    content=TextBlock(text=f"Test doc for {metric}"),
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
        store.close()


async def main() -> None:
    """Run all examples."""
    print("\n" + "=" * 60)
    print("AlibabaCloud AnalyticDB MySQL Vector Store Test Suite")
    print("=" * 60)

    try:
        await example_operations()
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
