# GraphKnowledgeBase Test Suite

## Requirements

- **Neo4j**: Running with GDS plugin at `bolt://localhost:7687`
- **Python**: 3.8+ with virtual environment activated
- **API Key**: Valid DashScope API key in environment or `conftest.py`

## Run Tests

```bash
# All tests (~7 min)
pytest tests/graph_rag/ -v

# Fast tests only (~10 sec)
pytest tests/graph_rag/ -v -m fast

# Skip slow tests (~3 min)
pytest tests/graph_rag/ -v -m "not slow"

# Skip community detection tests (no GDS needed)
pytest tests/graph_rag/ -v -m "not requires_gds"
```

## Test Coverage

### Modules (51 tests total)

| Module | Tests | Coverage |
|--------|-------|----------|
| Basic Operations | 9 | Initialization, document adding, embeddings |
| Search Modes | 9 | Vector, graph, hybrid, global search |
| Entity Extraction | 7 | LLM extraction, storage, embeddings |
| Relationship Extraction | 6 | Relation extraction, graph traversal |
| Community Detection | 6 | Leiden/Louvain algorithms, global search |
| Error Handling | 14 | Edge cases, validation |

### Features Tested

✅ **Vector Search** - Pure semantic similarity
✅ **Graph Search** - Relationship-based traversal
✅ **Hybrid Search** - Combined vector + graph
✅ **Global Search** - Community-level understanding
✅ **Entity Extraction** - LLM-powered entity identification
✅ **Relationship Extraction** - Entity relationship mapping
✅ **Community Detection** - Leiden & Louvain algorithms
✅ **Multi-hop Traversal** - 2-hop graph navigation
✅ **Auto Cleanup** - Isolated test collections

## Test Results

**Status**: ✅ All Passed
**Total**: 51 tests
**Pass Rate**: 100%
**Duration**: ~6.5 minutes
**Environment**: Real Neo4j + DashScope API

### Integration Verified

- Neo4j database connection
- Neo4j GDS plugin (Leiden & Louvain)
- DashScope Embedding API (text-embedding-v2, 1536 dims)
- DashScope LLM API (qwen-max)
- Vector index creation and search
- Graph traversal and relationship queries
- Async operations
- Automatic data cleanup

## Quick Reference

```bash
# By speed
pytest tests/graph_rag/ -v -m fast      # <5s tests
pytest tests/graph_rag/ -v -m medium    # 5-15s tests
pytest tests/graph_rag/ -v -m slow      # >15s tests

# By feature
pytest tests/graph_rag/test_basic_operations.py -v
pytest tests/graph_rag/test_search_modes.py -v
pytest tests/graph_rag/test_entity_extraction.py -v
pytest tests/graph_rag/test_relationship.py -v
pytest tests/graph_rag/test_community.py -v
pytest tests/graph_rag/test_error_handling.py -v
```
