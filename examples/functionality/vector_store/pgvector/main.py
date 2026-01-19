# -*- coding: utf-8 -*-
"""Example of using PgVectorStore in AgentScope RAG system."""
import asyncio
from agentscope.rag import (
    PgVectorStore,
    Document,
    DocMetadata,
)
from agentscope.message import TextBlock


async def setup_database() -> None:
    """Setup PostgreSQL database and enable pgvector extension."""
    print("\n" + "=" * 60)
    print("Database Setup")
    print("=" * 60)

    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    except ImportError:
        print("✗ psycopg2 is not installed. Please install it with:")
        print("  pip install psycopg2-binary")
        return

    # Connect to PostgreSQL server (default postgres database)
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="your_password",
            database="postgres",  # Connect to default database first
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Create database if not exists
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'agentscope_test'",
        )
        if not cursor.fetchone():
            cursor.execute("CREATE DATABASE agentscope_test")
            print("✓ Created database 'agentscope_test'")
        else:
            print("✓ Database 'agentscope_test' already exists")

        cursor.close()
        conn.close()

        # Connect to the agentscope_test database and enable pgvector
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="your_password",
            database="agentscope_test",
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Enable pgvector extension
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("✓ Enabled pgvector extension")

        cursor.close()
        conn.close()

        print("✓ Database setup completed successfully")

    except Exception as e:
        print(f"✗ Database setup failed: {e}")
        print("\nPlease ensure:")
        print("1. PostgreSQL is running")
        print("2. Connection parameters are correct")
        print("3. pgvector extension is installed")
        raise


async def example_basic_operations() -> None:
    """The example of basic CRUD operations with PgVectorStore."""
    print("\n" + "=" * 60)
    print("Test 1: Basic CRUD Operations")
    print("=" * 60)

    # Initialize PgVectorStore with PostgreSQL connection
    store = PgVectorStore(
        host="localhost",
        port=5432,
        user="postgres",
        password="your_password",
        database="agentscope_test",
        table_name="test_vectors",
        dimensions=4,  # Small dimension for testing
        distance="COSINE",
    )

    print("✓ PgVectorStore initialized")

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

    # Add documents
    await store.add(test_docs)
    print(f"✓ Added {len(test_docs)} documents")

    # Search for similar documents
    query_embedding = [0.15, 0.25, 0.35, 0.45]
    results = await store.search(
        query_embedding=query_embedding,
        limit=2,
    )

    print(f"\n✓ Found {len(results)} similar documents:")
    for i, result in enumerate(results, 1):
        print(f"  {i}. {result.metadata.content}")
        print(f"     Doc ID: {result.metadata.doc_id}")
        print(f"     Distance: {result.score:.4f}")

    # Delete documents
    await store.delete(ids=["doc_1"])
    print("\n✓ Deleted document with id 'doc_1'")

    # Get client for advanced operations
    client = store.get_client()
    print(f"\n✓ Got PostgreSQL connection: {type(client).__name__}")

    # Clean up
    store.close()


async def example_filter_search() -> None:
    """The example of search with metadata filtering."""
    print("\n" + "=" * 60)
    print("Test 2: Search with Metadata Filtering")
    print("=" * 60)

    store = PgVectorStore(
        host="localhost",
        port=5432,
        user="postgres",
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
    print(f"✓ Added {len(docs)} documents with different categories")

    # Search with filter for programming-related documents
    query_embedding = [0.2, 0.3, 0.4, 0.5]
    results = await store.search(
        query_embedding=query_embedding,
        limit=5,
        filter="doc_id LIKE 'prog_%'",
    )

    print("\n✓ Search results (filtered by 'prog_' prefix):")
    for i, result in enumerate(results, 1):
        print(f"  {i}. {result.metadata.content}")
        print(f"     Doc ID: {result.metadata.doc_id}")
        print(f"     Distance: {result.score:.4f}")

    # Clean up
    store.close()


async def example_multiple_chunks() -> None:
    """The example of documents with multiple chunks."""
    print("\n" + "=" * 60)
    print("Test 3: Documents with Multiple Chunks")
    print("=" * 60)

    store = PgVectorStore(
        host="localhost",
        port=5432,
        user="postgres",
        password="your_password",
        database="agentscope_test",
        table_name="chunks_vectors",
        dimensions=4,
        distance="COSINE",
    )

    # Create a document split into multiple chunks
    chunks = [
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Chapter 1: Introduction to AI"),
                doc_id="book_1",
                chunk_id=0,
                total_chunks=3,
            ),
            embedding=[0.1, 0.2, 0.3, 0.4],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Chapter 2: Machine Learning Basics"),
                doc_id="book_1",
                chunk_id=1,
                total_chunks=3,
            ),
            embedding=[0.2, 0.3, 0.4, 0.5],
        ),
        Document(
            metadata=DocMetadata(
                content=TextBlock(text="Chapter 3: Deep Learning Advanced"),
                doc_id="book_1",
                chunk_id=2,
                total_chunks=3,
            ),
            embedding=[0.3, 0.4, 0.5, 0.6],
        ),
    ]

    await store.add(chunks)
    print(f"✓ Added document with {len(chunks)} chunks")

    # Search and verify chunk information
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
        print(f"     Distance: {result.score:.4f}")

    # Clean up
    store.close()


async def example_distance_metrics() -> None:
    """The example of different distance metrics."""
    print("\n" + "=" * 60)
    print("Test 4: Different Distance Metrics")
    print("=" * 60)

    # Test with different metrics
    metrics = ["COSINE", "L2", "IP"]

    for metric in metrics:
        print(f"\n--- Testing {metric} metric ---")
        store = PgVectorStore(
            host="localhost",
            port=5432,
            user="postgres",
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

        print(f"✓ {metric} metric: Distance = {results[0].score:.4f}")

        # Clean up
        store.close()


async def main() -> None:
    """Run all examples."""
    print("\n" + "=" * 60)
    print("PgVectorStore Comprehensive Test Suite")
    print("=" * 60)

    try:
        # Setup database and enable pgvector extension
        await setup_database()

        # Run all examples
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
