# Graph RAG Example

This example demonstrates how to use `GraphKnowledgeBase` with Neo4j for graph-based retrieval-augmented generation (RAG).

## Prerequisites

1. **Neo4j Database**: Install and run Neo4j locally or use a cloud instance
   - Default URI: `bolt://localhost:7687`
   - Default credentials: `neo4j` / `password`

   Start Neo4j with GDS plugin using Docker:
   ```bash
   docker run -d --rm \
     --name neo4j \
     -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/password \
     -e "NEO4J_PLUGINS=[\"graph-data-science\"]" \
     neo4j:latest
   ```

2. **Neo4j GDS Plugin** (optional): Required for community detection features
   - The Docker command above already includes the GDS plugin
   - For manual installation, add the Graph Data Science library to your Neo4j instance

3. **Environment Variables**:
   ```bash
   export DASHSCOPE_API_KEY="your-api-key"
   ```

## Features Demonstrated

### Example 1: Basic Vector-Only Mode
- Simple semantic search without graph features
- Fastest retrieval mode
- No LLM required

### Example 2: Entity and Relationship Extraction
- Automatic entity extraction from documents
- Relationship discovery between entities
- Multiple search modes: vector, graph, hybrid

### Example 3: Community Detection
- Hierarchical community detection using Leiden algorithm
- Global search across community summaries
- Comprehensive thematic understanding

## Search Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `vector` | Pure semantic similarity | Simple queries, fastest |
| `graph` | Entity relationship traversal | Entity-centric queries |
| `hybrid` | Combined vector + graph | Best overall quality |
| `global` | Community-level search | Thematic/overview questions |

## Usage

```bash
python graph_knowledge_example.py
```

## Configuration

Update the following variables in the script as needed:

```python
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"
```
