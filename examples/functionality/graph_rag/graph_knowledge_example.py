# -*- coding: utf-8 -*-
"""Example: Using GraphKnowledgeBase with Neo4j.

This example demonstrates how to use the GraphKnowledgeBase with different
configurations and search modes.
"""

import asyncio
import os

from agentscope.embedding import DashScopeTextEmbedding
from agentscope.model import DashScopeChatModel
from agentscope.rag import (
    Document,
    DocMetadata,
    GraphKnowledgeBase,
    Neo4jGraphStore,
)

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not DASHSCOPE_API_KEY:
    raise ValueError("place set DASHSCOPE_API_KEY")

# Neo4j
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"


async def example_basic_vector_only() -> None:
    """Example 1: Basic usage (vector-only, no graph features)."""
    print("\n" + "=" * 80)
    print("Example 1: Basic Vector-Only Mode (No Graph Features)")
    print("=" * 80)

    # Initialize Neo4j graph store
    graph_store = Neo4jGraphStore(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database="neo4j",
        collection_name="basic_knowledge",
        dimensions=1536,
    )

    # Initialize knowledge base (graph features disabled)
    knowledge = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v2",
            api_key=DASHSCOPE_API_KEY,
        ),
        llm_model=None,  # No LLM needed for vector-only mode
        enable_entity_extraction=False,
        enable_relationship_extraction=False,
        enable_community_detection=False,
    )

    # Create sample documents
    documents = [
        Document(
            id="doc1",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Alice works at OpenAI as a researcher "
                        "specializing in language models."
                    ),
                },
                doc_id="doc1",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc2",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "OpenAI is located in San Francisco and "
                        "develops advanced AI systems."
                    ),
                },
                doc_id="doc2",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc3",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Bob collaborates with Alice on transformer "
                        "architecture research."
                    ),
                },
                doc_id="doc3",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    # Add documents to knowledge base
    print("\n📥 Adding documents...")
    await knowledge.add_documents(documents)
    print(f"✅ Added {len(documents)} documents")

    # Retrieve using vector search
    print("\n🔍 Searching: 'Where does Alice work?'")
    results = await knowledge.retrieve(
        query="Where does Alice work?",
        limit=2,
        search_mode="vector",
    )

    print(f"\n📄 Found {len(results)} results:")
    for i, doc in enumerate(results, 1):
        print(f"\n  {i}. [Score: {doc.score:.3f}]")
        print(f"     {doc.metadata.content['text']}")


async def example_with_graph_features() -> None:
    """Example 2: Full graph features (entities + relationships)."""
    print("\n" + "=" * 80)
    print("Example 2: With Graph Features (Entities + Relationships)")
    print("=" * 80)

    # Initialize Neo4j graph store
    graph_store = Neo4jGraphStore(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database="neo4j",
        collection_name="graph_knowledge",
        dimensions=1536,
    )

    # Initialize knowledge base with graph features
    knowledge = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v2",
            api_key=DASHSCOPE_API_KEY,
        ),
        llm_model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=DASHSCOPE_API_KEY,
            stream=False,  # Disable streaming for structured output
        ),
        enable_entity_extraction=True,
        entity_extraction_config={
            "max_entities_per_chunk": 10,
            "enable_gleanings": False,  # Disable for faster processing
        },
        enable_relationship_extraction=True,
        enable_community_detection=False,  # Disable for this example
    )

    # Create sample documents
    documents = [
        Document(
            id="doc4",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Alice works at OpenAI as a researcher "
                        "specializing in language models."
                    ),
                },
                doc_id="doc4",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc5",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "OpenAI is located in San Francisco and "
                        "develops advanced AI systems."
                    ),
                },
                doc_id="doc5",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc6",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Bob collaborates with Alice on transformer "
                        "architecture research."
                    ),
                },
                doc_id="doc6",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    # Add documents (will extract entities and relationships)
    print("\n📥 Adding documents (with entity/relationship extraction)...")
    await knowledge.add_documents(documents)
    print(f"✅ Added {len(documents)} documents with graph features")

    # Test different search modes
    query = "Tell me about Alice's work"

    # Vector search
    print(f"\n🔍 [Vector Search] Query: '{query}'")
    vector_results = await knowledge.retrieve(
        query=query,
        limit=2,
        search_mode="vector",
    )
    print(f"   Found {len(vector_results)} results")

    # Graph search
    print(f"\n🔍 [Graph Search] Query: '{query}'")
    graph_results = await knowledge.retrieve(
        query=query,
        limit=2,
        search_mode="graph",
        max_hops=2,
    )
    print(f"   Found {len(graph_results)} results")

    # Hybrid search (recommended)
    print(f"\n🔍 [Hybrid Search] Query: '{query}'")
    hybrid_results = await knowledge.retrieve(
        query=query,
        limit=2,
        search_mode="hybrid",
        vector_weight=0.5,
        graph_weight=0.5,
    )
    print(f"   Found {len(hybrid_results)} results")

    print("\n📄 Hybrid search results:")
    for i, doc in enumerate(hybrid_results, 1):
        print(f"\n  {i}. [Score: {doc.score:.3f}]")
        print(f"     {doc.metadata.content['text']}")


async def example_with_community_detection() -> None:  # pylint: disable=R0915
    """
    Example 3:
    Full features including community detection and global search.
    """

    # Initialize Neo4j graph store
    graph_store = Neo4jGraphStore(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database="neo4j",
        collection_name="full_knowledge",
        dimensions=1536,
    )

    # Initialize knowledge base with all features
    knowledge = GraphKnowledgeBase(
        graph_store=graph_store,
        embedding_model=DashScopeTextEmbedding(
            model_name="text-embedding-v2",
            api_key=DASHSCOPE_API_KEY,
        ),
        llm_model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=DASHSCOPE_API_KEY,
            stream=False,  # Disable streaming for structured output
        ),
        enable_entity_extraction=True,
        entity_extraction_config={
            "max_entities_per_chunk": 15,
            "enable_gleanings": False,  # Disable for faster processing
            "gleanings_rounds": 1,
        },
        enable_relationship_extraction=True,
        enable_community_detection=True,  # Enable community detection
        community_algorithm="leiden",
    )

    # Create diverse sample documents with different relevance levels
    # This helps test the scoring algorithm's ability to distinguish quality
    documents = [
        # === HIGH RELEVANCE: Core AI research organizations and topics ===
        Document(
            id="doc7",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "OpenAI conducts cutting-edge research in "
                        "artificial intelligence, focusing on large language "
                        "models, reinforcement learning, and neural network "
                        "architectures like GPT-4 and transformer models."
                    ),
                },
                doc_id="doc7",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc8",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Google DeepMind in London is a leading AI research "
                        "laboratory that pioneered breakthroughs in deep "
                        "reinforcement learning, including AlphaGo, AlphaFold"
                        "and various machine learning techniques."
                    ),
                },
                doc_id="doc8",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc9",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Microsoft Research AI division collaborates with "
                        "OpenAI to advance artificial intelligence "
                        "research, integrating AI capabilities into Azure "
                        "cloud platform and developing neural language "
                        "models."
                    ),
                },
                doc_id="doc9",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # === MEDIUM RELEVANCE: Related but not core focus ===
        Document(
            id="doc10",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Alice works as a software engineer at a tech "
                        "startup in San Francisco, where she occasionally "
                        "uses machine learning libraries for data analysis "
                        "tasks."
                    ),
                },
                doc_id="doc10",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc11",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Bob is studying computer science at Stanford "
                        "University and taking courses in algorithms, data "
                        "structures, and an introductory class on artificial "
                        "intelligence."
                    ),
                },
                doc_id="doc11",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        # === LOW RELEVANCE: Tangentially related or different topics ===
        Document(
            id="doc12",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "The Python programming language is widely used in "
                        "software development for web applications, data "
                        "analysis, and automation scripts across various "
                        "industries."
                    ),
                },
                doc_id="doc12",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc13",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "San Francisco is a major city in California known "
                        "for its Golden Gate Bridge, diverse culture, "
                        "tech industry "
                        "presence, and foggy weather patterns."
                    ),
                },
                doc_id="doc13",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
        Document(
            id="doc14",
            metadata=DocMetadata(
                content={
                    "type": "text",
                    "text": (
                        "Cloud computing services like Azure, AWS, and Google "
                        "Cloud provide scalable infrastructure for businesses "
                        "to host applications, store data, and manage "
                        "workloads efficiently."
                    ),
                },
                doc_id="doc14",
                chunk_id=0,
                total_chunks=1,
            ),
        ),
    ]

    print("\n📥 Adding documents (with all features)...")
    print(f"   Total documents: {len(documents)}")
    await knowledge.add_documents(documents)
    print(f"✅ Added {len(documents)} documents with:")
    print("   - Entity extraction")
    print("   - Relationship extraction")
    print("   - Community detection (triggered in background)")

    # Wait a bit for community detection to complete
    print("\n⏳ Waiting for background community detection...")
    await asyncio.sleep(8)

    # Manually trigger community detection again to ensure it's complete
    print("\n🔬 Running community detection...")
    try:
        result = await knowledge.detect_communities()
        print("✅ Community detection completed:")
        print(f"   - Communities detected: {result['community_count']}")
        print(f"   - Hierarchical levels: {result['levels']}")
        print(f"   - Algorithm used: {result['algorithm']}")
        print(f"   - Status: {result.get('status', 'unknown')}")
    except Exception as e:
        print(f"   ⚠️  Community detection error: {e}")
        print("   Continuing with other search modes...")

    # Compare different search modes
    print("\n" + "=" * 80)
    print("Comparing Search Modes")
    print("=" * 80)

    query = "What are the main AI research topics and organizations?"
    print(f"\n🔍 Query: '{query}'")
    print("\n" + "-" * 80)

    # 1. Vector Search
    print("\n1️⃣  Vector Search (baseline - pure semantic similarity)")
    try:
        vector_results = await knowledge.retrieve(
            query=query,
            limit=3,
            search_mode="vector",
        )
        print(f"   ✅ Found {len(vector_results)} results")
        for i, doc in enumerate(vector_results, 1):
            print(
                f"   {i}. [Score: {doc.score:.3f}] "
                f"{doc.metadata.content['text'][:80]}...",
            )
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 2. Graph Search
    print("\n2️⃣  Graph Search (uses entity relationships)")
    try:
        graph_results = await knowledge.retrieve(
            query=query,
            limit=3,
            search_mode="graph",
            max_hops=2,
        )
        print(f"   ✅ Found {len(graph_results)} results")
        for i, doc in enumerate(graph_results, 1):
            print(
                f"   {i}. [Score: {doc.score:.3f}] "
                f"{doc.metadata.content['text'][:80]}...",
            )
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 3. Hybrid Search
    print("\n3️⃣  Hybrid Search (vector + graph combined)")
    try:
        hybrid_results = await knowledge.retrieve(
            query=query,
            limit=3,
            search_mode="hybrid",
            vector_weight=0.5,
            graph_weight=0.5,
        )
        print(f"   ✅ Found {len(hybrid_results)} results")
        for i, doc in enumerate(hybrid_results, 1):
            print(
                f"   {i}. [Score: {doc.score:.3f}] "
                f"{doc.metadata.content['text'][:80]}...",
            )
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 4. Global Search (NEW - fully implemented!)
    print("\n4️⃣  Global Search (community-level understanding) ⭐ NEW")
    try:
        global_results = await knowledge.retrieve(
            query=query,
            limit=3,
            search_mode="global",
            min_community_level=0,
            max_entities_per_community=10,
            community_limit=5,
        )
        print(f"   ✅ Found {len(global_results)} results")
        for i, doc in enumerate(global_results, 1):
            print(
                f"   {i}. [Score: {doc.score:.3f}] "
                f"{doc.metadata.content['text'][:80]}...",
            )

        # Show detailed results for global search
        if global_results:
            print("\n📄 Global Search - Detailed Results:")
            for i, doc in enumerate(global_results, 1):
                print(f"\n  {i}. Score: {doc.score:.4f}")
                print(f"     Content: {doc.metadata.content['text']}")
    except ValueError as e:
        print(f"   ⚠️  {e}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback

        traceback.print_exc()

    print("\n" + "=" * 80)
    print("💡 Search Mode Recommendations:")
    print("   - Use 'vector' for simple semantic search (fastest)")
    print("   - Use 'graph' when relationships matter (entity-centric)")
    print("   - Use 'hybrid' for best overall quality (recommended)")
    print(
        "   - Use 'global' for thematic/overview questions "
        "(slowest, most comprehensive)",
    )
    print("=" * 80)


async def main() -> None:
    """Run all examples."""
    print("\n" + "=" * 80)
    print("GraphKnowledgeBase Usage Examples")
    print("=" * 80)
    print("\n📌 Configuration:")
    print(f"   - Neo4j URI: {NEO4J_URI}")
    print(f"   - Neo4j User: {NEO4J_USER}")
    print(f"   - DashScope API Key: {os.environ['DASHSCOPE_API_KEY'][:20]}...")
    print("\n⚠️  Please ensure:")
    print("   1. Neo4j is running")
    print("   2. NEO4J_PASSWORD is updated in the script")

    try:
        # Example 1: Basic vector retrieval
        await example_basic_vector_only()

        # Ask whether to continue
        print("\n" + "=" * 80)
        user_input = input("\nContinue with Example 2 (entity extraction)? (y/n): ")
        if user_input.lower() == "y":
            await example_with_graph_features()

        # Ask whether to run community detection
        print("\n" + "=" * 80)
        user_input = input(
            "\nContinue with Example 3 (community detection)? "
            "(requires GDS plugin) (y/n): ",
        )
        if user_input.lower() == "y":
            await example_with_community_detection()

        print("\n" + "=" * 80)
        print("✅ All examples completed!")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
