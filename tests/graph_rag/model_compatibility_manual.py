# -*- coding: utf-8 -*-
"""GraphKnowledgeBase Model Compatibility Test.

This script tests GraphKnowledgeBase with different embedding and LLM model
combinations to verify compatibility. It tests at least:
- Ollama embedding + Ollama LLM
- OpenAI embedding + OpenAI LLM
- Mixed combinations

Note: This is NOT a pytest test file. Run it directly with:
    python test_model_compatibility.py

IMPORTANT - Clean Up Neo4j Before Running:
===========================================
If you have old data/indexes in Neo4j from previous runs, you may encounter
dimension mismatch errors. To clean up, connect to Neo4j and run:

    # Delete all nodes and relationships (WARNING: deletes ALL data!)
    MATCH (n) DETACH DELETE n

    # Show all indexes to see what needs to be dropped
    SHOW INDEXES

    # Drop specific vector indexes (replace INDEX_NAME with actual name)
    DROP INDEX INDEX_NAME IF EXISTS

    # Or drop all indexes matching a pattern (Neo4j 5.x+)
    # Example: DROP INDEX document_vector_basic_knowledge IF EXISTS

Alternative: Use unique collection names for each test run to avoid conflicts.
"""

import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

from agentscope.embedding import (
    DashScopeTextEmbedding,
    EmbeddingModelBase,
    OllamaTextEmbedding,
    OpenAITextEmbedding,
)
from agentscope.model import (
    ChatModelBase,
    DashScopeChatModel,
    OllamaChatModel,
    OpenAIChatModel,
)
from agentscope.rag import (
    Document,
    DocMetadata,
    GraphKnowledgeBase,
    Neo4jGraphStore,
)

# Add src to path if needed for development
src_path = Path(__file__).parent.parent.parent / "src"
if src_path.exists() and str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# ============================================================================
# Configuration
# ============================================================================

# Neo4j Configuration (using local Neo4j with plugins)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# API Keys (optional - will skip tests if not provided)
# Using SiliconFlow (OpenAI-compatible API)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# Ollama Configuration (assumes local Ollama is running)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://192.168.1.237:11434")
OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "bge-large:latest",
)
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen3:4b")

OPENAI_EMBEDDING_MODEL = "BAAI/bge-large-zh-v1.5"
OPENAI_EMBEDDING_DIMENSIONS = 1024  # bge-large-zh-v1.5 uses 1024 dimensions
OPENAI_LLM_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # SiliconFlow LLM model

# Test Configuration
TEST_VECTOR_ONLY = True  # Test vector-only mode (no LLM needed)
TEST_WITH_GRAPH_FEATURES = True  # Test with entity/relationship extraction
TEST_OLLAMA = True  # Enable Ollama tests

# ============================================================================
# Test Data
# ============================================================================

SIMPLE_DOCUMENTS = [
    Document(
        id="simple_1",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": (
                    "Alice works at OpenAI as a researcher specializing "
                    "in language models."
                ),
            },
            doc_id="simple_1",
            chunk_id=0,
            total_chunks=1,
        ),
    ),
    Document(
        id="simple_2",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": (
                    "Bob collaborates with Alice on transformer "
                    "architecture research at OpenAI."
                ),
            },
            doc_id="simple_2",
            chunk_id=0,
            total_chunks=1,
        ),
    ),
    Document(
        id="simple_3",
        metadata=DocMetadata(
            content={
                "type": "text",
                "text": (
                    "OpenAI is located in San Francisco and develops "
                    "advanced AI systems."
                ),
            },
            doc_id="simple_3",
            chunk_id=0,
            total_chunks=1,
        ),
    ),
]

# ============================================================================
# Test Results Tracking
# ============================================================================


class TestResult:
    """Track test results."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.success = False
        self.error = None
        self.duration = 0.0
        self.details = {}

    def __str__(self) -> str:
        status = "✅ PASS" if self.success else "❌ FAIL"
        result = f"{status} {self.name} ({self.duration:.2f}s)"
        if self.error:
            result += f"\n    Error: {self.error}"
        if self.details:
            for key, value in self.details.items():
                result += f"\n    {key}: {value}"
        return result


test_results = []

# ============================================================================
# Helper Functions
# ============================================================================


def print_section(title: str, char: str = "=") -> None:
    """Print a section header."""
    print(f"\n{char * 80}")
    print(title)
    print(f"{char * 80}\n")


async def cleanup_collection(graph_store: Neo4jGraphStore) -> None:
    """Clean up test collection from Neo4j."""
    try:
        driver = graph_store.get_client()
        async with driver.session(database=NEO4J_DATABASE) as session:
            await session.run(
                """
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label ENDS WITH $suffix)
                DETACH DELETE n
                """,
                {"suffix": f"_{graph_store.collection_name}"},
            )
        await graph_store.close()
    except Exception as e:
        print(f"⚠️  Warning: Failed to clean up collection: {e}")


async def test_vector_only_mode(
    name: str,
    embedding_model: EmbeddingModelBase,
    embedding_dimensions: int,
) -> None:
    """Test vector-only mode (no graph features).

    Args:
        name: Test name
        embedding_model: Embedding model instance
        embedding_dimensions: Embedding dimensions
    """
    result = TestResult(name)
    start_time = time.time()

    graph_store = None
    try:
        print(f"\n▶️  Running: {name}")

        # Create graph store
        collection_name = f"compat_test_{int(time.time() * 1000)}"
        graph_store = Neo4jGraphStore(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            database=NEO4J_DATABASE,
            collection_name=collection_name,
            dimensions=embedding_dimensions,
        )

        # Create knowledge base (vector-only mode)
        knowledge = GraphKnowledgeBase(
            graph_store=graph_store,
            embedding_model=embedding_model,
            llm_model=None,  # No LLM for vector-only mode
            enable_entity_extraction=False,
            enable_relationship_extraction=False,
            enable_community_detection=False,
        )

        # Add documents
        print("  📥 Adding documents...")
        await knowledge.add_documents(
            SIMPLE_DOCUMENTS[:2],
        )  # Use only 2 docs for speed
        result.details["documents_added"] = 2

        # Test vector search
        print("  🔍 Testing vector search...")
        results = await knowledge.retrieve(
            query="Where does Alice work?",
            limit=2,
            search_mode="vector",
        )

        if len(results) > 0:
            result.details["search_results"] = len(results)
            result.details["top_score"] = f"{results[0].score:.3f}"
            result.success = True
            print(
                f"  ✅ Found {len(results)} results "
                f"(top score: {results[0].score:.3f})",
            )
        else:
            result.error = "No search results returned"
            print("  ❌ No search results")

    except Exception as e:
        result.error = str(e)
        print(f"  ❌ Error: {e}")
        traceback.print_exc()
    finally:
        result.duration = time.time() - start_time
        test_results.append(result)
        if graph_store:
            await cleanup_collection(graph_store)


async def test_with_graph_features(
    name: str,
    embedding_model: EmbeddingModelBase,
    llm_model: ChatModelBase,
    embedding_dimensions: int,
) -> None:
    """Test with graph features (entity/relationship extraction).

    Args:
        name: Test name
        embedding_model: Embedding model instance
        llm_model: LLM model instance
        embedding_dimensions: Embedding dimensions
    """
    result = TestResult(name)
    start_time = time.time()

    graph_store = None
    try:
        print(f"\n▶️  Running: {name}")

        # Create graph store
        collection_name = f"compat_test_{int(time.time() * 1000)}"
        graph_store = Neo4jGraphStore(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            database=NEO4J_DATABASE,
            collection_name=collection_name,
            dimensions=embedding_dimensions,
        )

        # Create knowledge base with graph features
        knowledge = GraphKnowledgeBase(
            graph_store=graph_store,
            embedding_model=embedding_model,
            llm_model=llm_model,
            enable_entity_extraction=True,
            enable_relationship_extraction=True,
            enable_community_detection=False,  # Skip community
            # detection for speed
            entity_extraction_config={
                "max_entities_per_chunk": 10,
                "enable_gleanings": False,  # Disable for speed
            },
        )

        # Add documents
        print("  📥 Adding documents with entity extraction...")
        await knowledge.add_documents(
            SIMPLE_DOCUMENTS[:2],
        )  # Use only 2 docs for speed
        result.details["documents_added"] = 2

        # Wait a bit for entity extraction
        print("  ⏳ Waiting for entity extraction...")
        await asyncio.sleep(3)

        # Test vector search
        print("  🔍 Testing vector search...")
        vector_results = await knowledge.retrieve(
            query="Tell me about Alice",
            limit=2,
            search_mode="vector",
        )
        result.details["vector_results"] = len(vector_results)

        # Test graph search
        print("  🔍 Testing graph search...")
        try:
            graph_results = await knowledge.retrieve(
                query="Tell me about Alice",
                limit=2,
                search_mode="graph",
                max_hops=2,
            )
            result.details["graph_results"] = len(graph_results)
        except Exception as e:
            result.details["graph_search_error"] = str(e)
            print(f"  ⚠️  Graph search failed: {e}")

        # Test hybrid search
        print("  🔍 Testing hybrid search...")
        try:
            hybrid_results = await knowledge.retrieve(
                query="Tell me about Alice",
                limit=2,
                search_mode="hybrid",
                vector_weight=0.5,
                graph_weight=0.5,
            )
            result.details["hybrid_results"] = len(hybrid_results)
        except Exception as e:
            result.details["hybrid_search_error"] = str(e)
            print(f"  ⚠️  Hybrid search failed: {e}")

        # Success if at least vector search worked
        if len(vector_results) > 0:
            result.success = True
            print("  ✅ Test completed successfully")
        else:
            result.error = "No search results returned"
            print("  ❌ No search results")

    except Exception as e:
        result.error = str(e)
        print(f"  ❌ Error: {e}")
        traceback.print_exc()
    finally:
        result.duration = time.time() - start_time
        test_results.append(result)
        if graph_store:
            await cleanup_collection(graph_store)


# ============================================================================
# Test Cases
# ============================================================================


async def test_ollama_models() -> None:
    """Test Ollama embedding + Ollama LLM."""
    if not TEST_OLLAMA:
        print("\n⚠️  Skipping Ollama tests (disabled in config)")
        return

    print_section("Testing Ollama Models", "=")

    try:
        # Test vector-only mode
        if TEST_VECTOR_ONLY:
            embedding_model = OllamaTextEmbedding(
                model_name=OLLAMA_EMBEDDING_MODEL,
                dimensions=1024,  # bge-large uses 1024 dimensions
                host=OLLAMA_HOST,
            )
            await test_vector_only_mode(
                name="Ollama Embedding (Vector-Only)",
                embedding_model=embedding_model,
                embedding_dimensions=1024,  # bge-large uses 1024 dimensions
            )

        # Test with graph features
        if TEST_WITH_GRAPH_FEATURES:
            embedding_model = OllamaTextEmbedding(
                model_name=OLLAMA_EMBEDDING_MODEL,
                dimensions=1024,  # bge-large uses 1024 dimensions
                host=OLLAMA_HOST,
            )
            llm_model = OllamaChatModel(
                model_name=OLLAMA_LLM_MODEL,
                host=OLLAMA_HOST,
                stream=False,
            )
            await test_with_graph_features(
                name="Ollama Embedding + Ollama LLM (Graph Features)",
                embedding_model=embedding_model,
                llm_model=llm_model,
                embedding_dimensions=1024,
            )
    except Exception as e:
        print(f"❌ Ollama tests failed: {e}")
        traceback.print_exc()


async def test_openai_models() -> None:
    """Test OpenAI embedding + OpenAI LLM."""
    if not OPENAI_API_KEY:
        print("\n⚠️  Skipping OpenAI tests (no API key)")
        return

    print_section("Testing OpenAI Models", "=")

    try:
        # Test vector-only mode
        if TEST_VECTOR_ONLY:
            embedding_model = OpenAITextEmbedding(
                model_name=OPENAI_EMBEDDING_MODEL,
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
                dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
            await test_vector_only_mode(
                name="OpenAI Embedding (Vector-Only)",
                embedding_model=embedding_model,
                embedding_dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )

        # Test with graph features
        if TEST_WITH_GRAPH_FEATURES:
            embedding_model = OpenAITextEmbedding(
                model_name=OPENAI_EMBEDDING_MODEL,
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
                dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
            llm_model = OpenAIChatModel(
                model_name=OPENAI_LLM_MODEL,
                api_key=OPENAI_API_KEY,
                client_args={"base_url": OPENAI_BASE_URL},
                stream=False,
            )
            await test_with_graph_features(
                name="OpenAI Embedding + OpenAI LLM (Graph Features)",
                embedding_model=embedding_model,
                llm_model=llm_model,
                embedding_dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
    except Exception as e:
        print(f"❌ OpenAI tests failed: {e}")
        traceback.print_exc()


async def test_mixed_ollama_openai() -> None:
    """Test Ollama embedding + OpenAI LLM."""
    if not TEST_OLLAMA or not OPENAI_API_KEY:
        print(
            "\n⚠️  Skipping Ollama+OpenAI mixed test "
            "(Ollama disabled or no OpenAI API key)",
        )
        return

    print_section("Testing Mixed: Ollama Embedding + OpenAI LLM", "=")

    try:
        if TEST_WITH_GRAPH_FEATURES:
            embedding_model = OllamaTextEmbedding(
                model_name=OLLAMA_EMBEDDING_MODEL,
                dimensions=1024,  # bge-large uses 1024 dimensions
                host=OLLAMA_HOST,
            )
            llm_model = OpenAIChatModel(
                model_name=OPENAI_LLM_MODEL,
                api_key=OPENAI_API_KEY,
                client_args={"base_url": OPENAI_BASE_URL},
                stream=False,
            )
            await test_with_graph_features(
                name="Ollama Embedding + OpenAI LLM (Mixed)",
                embedding_model=embedding_model,
                llm_model=llm_model,
                embedding_dimensions=1024,
            )
    except Exception as e:
        print(f"❌ Mixed Ollama+OpenAI test failed: {e}")
        traceback.print_exc()


async def test_mixed_openai_ollama() -> None:
    """Test OpenAI embedding + Ollama LLM."""
    if not TEST_OLLAMA or not OPENAI_API_KEY:
        print(
            "\n⚠️  Skipping OpenAI+Ollama mixed test "
            "(Ollama disabled or no OpenAI API key)",
        )
        return

    print_section("Testing Mixed: OpenAI Embedding + Ollama LLM", "=")

    try:
        if TEST_WITH_GRAPH_FEATURES:
            embedding_model = OpenAITextEmbedding(
                model_name=OPENAI_EMBEDDING_MODEL,
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
                dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
            llm_model = OllamaChatModel(
                model_name=OLLAMA_LLM_MODEL,
                host=OLLAMA_HOST,
                stream=False,
            )
            await test_with_graph_features(
                name="OpenAI Embedding + Ollama LLM (Mixed)",
                embedding_model=embedding_model,
                llm_model=llm_model,
                embedding_dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
    except Exception as e:
        print(f"❌ Mixed OpenAI+Ollama test failed: {e}")
        traceback.print_exc()


async def test_dashscope_models() -> None:
    """Test DashScope embedding + DashScope LLM (optional)."""
    if not DASHSCOPE_API_KEY:
        print("\n⚠️  Skipping DashScope tests (no API key)")
        return

    print_section("Testing DashScope Models (Optional)", "=")

    try:
        # Test vector-only mode
        if TEST_VECTOR_ONLY:
            embedding_model = DashScopeTextEmbedding(
                model_name="text-embedding-v2",
                api_key=DASHSCOPE_API_KEY,
            )
            await test_vector_only_mode(
                name="DashScope Embedding (Vector-Only)",
                embedding_model=embedding_model,
                embedding_dimensions=1536,
            )

        # Test with graph features
        if TEST_WITH_GRAPH_FEATURES:
            embedding_model = DashScopeTextEmbedding(
                model_name="text-embedding-v2",
                api_key=DASHSCOPE_API_KEY,
            )
            llm_model = DashScopeChatModel(
                model_name="qwen-max",
                api_key=DASHSCOPE_API_KEY,
                stream=False,
            )
            await test_with_graph_features(
                name="DashScope Embedding + DashScope LLM (Graph Features)",
                embedding_model=embedding_model,
                llm_model=llm_model,
                embedding_dimensions=1536,
            )
    except Exception as e:
        print(f"❌ DashScope tests failed: {e}")
        traceback.print_exc()


# ============================================================================
# Main Test Runner
# ============================================================================


async def main() -> int:
    """Run all compatibility tests."""
    print_section("GraphKnowledgeBase Model Compatibility Test", "=")

    print("📌 Configuration:")
    print(f"   Neo4j URI: {NEO4J_URI}")
    print(f"   Neo4j User: {NEO4J_USER}")
    print(f"   Neo4j Database: {NEO4J_DATABASE}")
    print(f"   Ollama Host: {OLLAMA_HOST}")
    print(f"   Ollama Embedding Model: {OLLAMA_EMBEDDING_MODEL}")
    print(f"   Ollama LLM Model: {OLLAMA_LLM_MODEL}")
    print(
        f"   OpenAI API Key: " f"{'✅ Set' if OPENAI_API_KEY else '❌ Not Set'}",
    )
    print(f"   OpenAI Base URL: {OPENAI_BASE_URL}")
    print(
        f"   DashScope API Key: "
        f"{'✅ Set' if DASHSCOPE_API_KEY else '❌ Not Set'}",
    )

    print("\n⚠️  Please ensure:")
    print("   1. Neo4j is running locally with GDS plugin")
    print("   2. Ollama is running locally with required models")
    print(
        "   3. API keys are set in environment variables "
        "(if testing those providers)",
    )

    print("\n📋 Test Plan:")
    print("   - Ollama models (required)")
    print("   - OpenAI models (if API key available)")
    print("   - Mixed combinations (if API keys available)")
    print("   - DashScope models (optional, if API key available)")

    start_time = time.time()

    # Run tests
    await test_ollama_models()
    await test_openai_models()
    await test_mixed_ollama_openai()
    await test_mixed_openai_ollama()
    await test_dashscope_models()

    # Print summary
    total_duration = time.time() - start_time
    print_section("Test Summary", "=")

    for result in test_results:
        print(result)

    # Statistics
    total_tests = len(test_results)
    passed_tests = sum(1 for r in test_results if r.success)
    failed_tests = total_tests - passed_tests

    print(f"\n{'-' * 80}")
    print(f"Total Tests: {total_tests}")
    print(f"Passed: {passed_tests} ✅")
    print(f"Failed: {failed_tests} ❌")
    print(f"Total Duration: {total_duration:.2f}s")

    if failed_tests == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {failed_tests} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
