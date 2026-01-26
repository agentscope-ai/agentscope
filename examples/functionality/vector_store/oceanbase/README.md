# OceanBase Vector Store

This example demonstrates how to use **OceanBaseStore** for vector storage and semantic search in AgentScope.
It includes CRUD operations, metadata filtering, document chunking, and distance metric tests.

### Quick Start

Install dependencies (including `pyobvector`):

```bash
pip install -e .[full]
```

Start seekdb (a minimal OceanBase-compatible instance):

```bash
docker run -d -p 2881:2881 oceanbase/seekdb
```

Run the example script:

```bash
python main.py
```

> **Note:** The script defaults to `127.0.0.1:2881`, user `root`, database `test`.
> If you use a multi-tenant OceanBase account (e.g., `root@test`), override via environment variables.

## Usage

### Initialize Store

```python
from agentscope.rag import OceanBaseStore

store = OceanBaseStore(
    collection_name="test_collection",
    dimensions=768,
    distance="COSINE",
    uri="127.0.0.1:2881",
    user="root",
    password="",
    db_name="test",
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
    embedding=[0.1, 0.2, 0.3],
)

await store.add([doc])
```

### Search

```python
results = await store.search(
    query_embedding=[0.1, 0.2, 0.3],
    limit=5,
    score_threshold=0.9,
)
```

### Filter Search

```python
client = store.get_client()
table = client.load_table(collection_name="test_collection")

results = await store.search(
    query_embedding=[0.1, 0.2, 0.3],
    limit=5,
    flter=[table.c["doc_id"].like("doc%")],
)
```

### Delete

```python
client = store.get_client()
table = client.load_table(collection_name="test_collection")

await store.delete(where=[table.c["doc_id"] == "doc_1"])
```

## Distance Metrics

| Metric | Description | Best For |
|--------|-------------|----------|
| **COSINE** | Cosine similarity | Text embeddings (recommended) |
| **L2** | Euclidean distance | Spatial data |
| **IP** | Inner product | Recommendation systems |

## Filter Expressions

Build filters using SQLAlchemy expressions and pass them via `flter`:

```python
table = store.get_client().load_table("test_collection")

filters = [
    table.c["doc_id"] == "doc_1",
    table.c["doc_id"].like("prefix%"),
    table.c["chunk_id"] >= 0,
]
```

## Advanced Usage

### Access Underlying Client

```python
client = store.get_client()
stats = client.get_collection_stats(collection_name="test_collection")
```

### Document Metadata

- `content`: Text content (TextBlock)
- `doc_id`: Unique document identifier
- `chunk_id`: Chunk position (0-indexed)
- `total_chunks`: Total chunks in document

## FAQ

**What embedding dimension should I use?**
Match your embedding model's output dimension (e.g., 768 for BERT, 1536 for OpenAI ada-002).

**Can I change the distance metric after creation?**
No, create a new collection with the desired metric.

**How do I clean up test data?**
Drop the collection via the underlying client or remove the seekdb container volume.

## Environment Variables

The script supports the following environment variables to override connection settings:

```bash
export OCEANBASE_URI="127.0.0.1:2881"
export OCEANBASE_USER="root"
export OCEANBASE_PASSWORD=""
export OCEANBASE_DB="test"
```

## References

- [OceanBase Vector Store](https://github.com/oceanbase/pyobvector)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)
