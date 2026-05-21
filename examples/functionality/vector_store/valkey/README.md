# Valkey Vector Store

This example demonstrates how to use **ValkeyStore** for vector storage and
semantic search in AgentScope using Valkey's Search module with HNSW indexing.

### Quick Start

Install agentscope first, and then the Valkey dependency:

```bash
pip install "valkey-glide>=2.1.0,<3.0.0"
```

**Prerequisites:** A Valkey server with the Search module loaded on
`localhost:6379`. The easiest way is using the bundle image:

```bash
docker run -d --name valkey -p 6379:6379 valkey/valkey-bundle:latest
```

Run the example script:

```bash
python main.py
```

## Usage

### Initialize Store

```python
from agentscope.rag import ValkeyStore

# Basic standalone connection
store = ValkeyStore(
    host="localhost",
    port=6379,
    index_name="my_index",
    prefix="myapp:doc:",
    dimensions=768,
    distance="COSINE",
)

# Cluster mode with TLS
store = ValkeyStore(
    host="my-cluster.example.com",
    port=6379,
    dimensions=768,
    use_tls=True,
    use_cluster=True,
)

# Custom HNSW parameters for higher recall
store = ValkeyStore(
    host="localhost",
    port=6379,
    dimensions=768,
    hnsw_m=32,
    hnsw_ef_construction=400,
    hnsw_ef_runtime=100,
)
```

### Add Documents

```python
from agentscope.rag import Document, DocMetadata
from agentscope.message import TextBlock

doc = Document(
    metadata=DocMetadata(
        content=TextBlock(type="text", text="Your document text"),
        doc_id="doc_1",
        chunk_id=0,
        total_chunks=1,
    ),
    embedding=[0.1, 0.2, ...],  # Your embedding vector
)

await store.add([doc])
```

### Search

```python
# Basic search
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    score_threshold=0.8,
)

# Search with filter expression
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    filter_expression="@doc_id:{my_doc}",
)
```

### Delete

```python
# Delete by document ID (removes all chunks)
await store.delete(ids=["doc_1", "doc_2"])

# Delete specific keys directly
await store.delete(keys=["myapp:doc:some-uuid"])
```

### Cleanup

```python
# Drop the search index (keeps data)
await store.drop_index()

# Close the connection
await store.close()
```

## Distance Metrics

| Metric | Description | Best For |
|--------|-------------|----------|
| **COSINE** | Cosine similarity | Text embeddings (recommended) |
| **L2** | Euclidean distance | Spatial data |
| **IP** | Inner product | Normalized embeddings |

## Advanced Usage

### Access Underlying Client

```python
client = store.get_client()
# Use the Glide client for advanced Valkey operations
await client.ping()
```

### HNSW Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `hnsw_m` | Max edges per node | Valkey default (16) |
| `hnsw_ef_construction` | Vectors examined during indexing | Valkey default (200) |
| `hnsw_ef_runtime` | Vectors examined during search | Valkey default (10) |
| `initial_cap` | Initial index capacity | Auto |

Higher `hnsw_m` and `hnsw_ef_*` values improve recall at the cost of memory
and latency.

## References

- [Valkey Search Documentation](https://valkey.io/topics/search/)
- [Valkey GLIDE Python Client](https://glide.valkey.io/)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)
