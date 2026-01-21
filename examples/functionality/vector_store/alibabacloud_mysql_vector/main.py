# -*- coding: utf-8 -*-
"""Example of using AlibabaCloudMySQLStore in AgentScope RAG system."""
import asyncio
from agentscope.rag import (
    AlibabaCloudMySQLStore,
    Document,
    DocMetadata,
)
from agentscope.message import TextBlock


async def example_basic_operations() -> None:
    """The example of basic CRUD operations with AlibabaCloudMySQLStore."""
    print("\n" + "=" * 60)
    print("Test 1: Basic CRUD Operations")
    print("=" * 60)

    # Initialize AlibabaCloudMySQLStore
    # Replace with your AlibabaCloud MySQL connection details
    store = AlibabaCloudMySQLStore(
        host="rm-xxxxx.mysql.rds.aliyuncs.com",  # Your RDS endpoint
        port=3306,
        user="your_username",
        password="your_password",
        database="agentscope_test",
        table_name="test_vectors",
        dimensions=4,  # Small dimension for testing
        distance="COSINE",
    )

    print("✓ AlibabaCloudMySQLStore initialized")

    # Create test documents with embeddings
    test_docs = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(
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
                content=TextBlock(text="Machine Learning is a subset of AI"),
                doc_id="doc_2",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.2, 0.3, 0.4, 0.5],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Deep Learning uses neural networks"),
                doc_id="doc_3",
                chunk_id=0,
                total_chunks=1,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
    ]

    # Test add operation
    await store.add(test_docs)
    print(f"✓ Added {len(test_docs)} documents to the store")

    # Test search operation
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

    # Test search with score threshold
    results_filtered = await store.search(
        query_embedding=query_embedding,
        limit=5,
        score_threshold=0.9,
    )
    print(f"\n✓ Search with threshold (>0.9): {len(results_filtered)} results")

    # Test delete operation
    await store.delete(filter='doc_id = "doc_2"')
    print("\n✓ Deleted document with doc_id='doc_2'")

    # Verify deletion
    results_after_delete = await store.search(
        query_embedding=query_embedding,
        limit=5,
    )
    print(f"✓ After deletion: {len(results_after_delete)} documents remain")

    # Get client for advanced operations
    client = store.get_client()
    print(f"\n✓ Got MySQL connection: {type(client).__name__}")

    # Close connection
    store.close()
    print("✓ Connection closed")


async def example_filter_search() -> None:
    """The example of search with metadata filtering."""
    print("\n" + "=" * 60)
    print("Test 2: Search with Metadata Filtering")
    print("=" * 60)

    store = AlibabaCloudMySQLStore(
        host="rm-xxxxx.mysql.rds.aliyuncs.com",
        port=3306,
        user="your_username",
        password="your_password",
        database="agentscope_test",
        table_name="filter_vectors",
        dimensions=4,
        distance="COSINE",
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

    # Search without filter
    query_embedding = [0.25, 0.35, 0.45, 0.55]
    all_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
    )
    print(f"\n✓ Search without filter: {len(all_results)} results")
    for i, result in enumerate(all_results, 1):
        doc_id = result.metadata.doc_id
        score = result.score
        print(f"  {i}. Doc ID: {doc_id}, Score: {score:.4f}")

    # Search with filter for programming docs
    prog_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
        filter='doc_id LIKE "prog%"',
    )
    filter_msg = 'doc_id LIKE "prog%"'
    print(f"\n✓ Search with filter ({filter_msg}): {len(prog_results)}")
    for i, result in enumerate(prog_results, 1):
        doc_id = result.metadata.doc_id
        score = result.score
        print(f"  {i}. Doc ID: {doc_id}, Score: {score:.4f}")

    # Search with filter for AI docs
    ai_results = await store.search(
        query_embedding=query_embedding,
        limit=4,
        filter='doc_id LIKE "ai%"',
    )
    filter_msg = 'doc_id LIKE "ai%"'
    print(f"\n✓ Search with filter ({filter_msg}): {len(ai_results)}")
    for i, result in enumerate(ai_results, 1):
        doc_id = result.metadata.doc_id
        score = result.score
        print(f"  {i}. Doc ID: {doc_id}, Score: {score:.4f}")

    store.close()


async def example_distance_metrics() -> None:
    """The example of different distance metrics."""
    print("\n" + "=" * 60)
    print("Test 3: Different Distance Metrics")
    print("=" * 60)

    # Test with different metrics
    # Note: AlibabaCloud MySQL only supports COSINE and EUCLIDEAN
    metrics = ["COSINE", "EUCLIDEAN"]

    for metric in metrics:
        print(f"\n--- Testing {metric} metric ---")
        store = AlibabaCloudMySQLStore(
            host="rm-xxxxx.mysql.rds.aliyuncs.com",
            port=3306,
            user="your_username",
            password="your_password",
            database="agentscope_test",
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
    print("AlibabaCloud MySQL Vector Store Test Suite")
    print("=" * 60)

    try:
        await example_basic_operations()
        await example_filter_search()
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
