### For Local Development (Optional - With Neo4j)
```bash
# Start Neo4j with GDS plugin
docker run -d --rm \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  -e "NEO4J_PLUGINS=[\"graph-data-science\"]" \
  neo4j:latest

```

## 🚀 Running Tests

```bash

# Run tests with Neo4j
NEO4J_AVAILABLE=true pytest tests/graph_rag/ -v

# Run all tests (mock mode - works in GitHub Actions)
pytest tests/graph_rag/ -v

# Skip tests requiring Neo4j GDS plugin
pytest tests/graph_rag/ -v -m "not requires_gds"

```

## 📝 Note on GDS Community Detection Tests

Community detection tests require real API models and are not included in the test suite.

**To test GDS features:** Use `examples/graph_rag/graph_knowledge_example.py` with real DashScope API credentials.
