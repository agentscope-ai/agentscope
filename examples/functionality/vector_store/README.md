# Vector Store Example

This example demonstrates how to use **MilvusLiteStore** for vector storage and semantic search in AgentScope. It includes four test scenarios covering CRUD operations, metadata filtering, document chunking, and distance metrics.

## Key Features

- **Vector Storage**: Store documents with embeddings in a lightweight Milvus Lite database
- **Semantic Search**: Perform similarity searches using vector embeddings
- **Metadata Filtering**: Filter search results based on document metadata
- **Multiple Distance Metrics**: Support for COSINE, L2, and Inner Product (IP)
- **Document Chunking**: Handle documents split into multiple chunks
- **Local Storage**: Serverless, file-based vector database

## Examples Included

1. **Basic CRUD Operations**: Initialize, add, search, delete documents
2. **Metadata Filtering**: Filter search results by document ID patterns
3. **Multiple Chunks**: Handle large documents split into smaller chunks
4. **Distance Metrics**: Compare COSINE, L2, and IP metrics

## How to Run

Install dependencies and run the example:

```bash
pip install agentscope pymilvus
python milvuslite_store.py
```

The script will run four test scenarios and display results for each.

> **Note:** The script creates `.db` files in the current directory. You can delete them after testing.

## Usage

### Initialize Store
```python
from agentscope.rag import MilvusLiteStore

store = MilvusLiteStore(
    uri="./milvus_test.db",
    collection_name="test_collection",
    dimensions=768,              # Match your embedding model
    distance="COSINE",           # COSINE, L2, or IP
)
```

### Add Documents
```python
from agentscope.rag import Document, DocMetadata
from agentscope.message import TextBlock

doc = Document(
    metadata=DocMetadata(
        content=TextBlock(text="Your document text"),
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
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    score_threshold=0.9,                # Optional
    filter='doc_id like "prefix%"',     # Optional
)
```

### Delete
```python
await store.delete(filter_expr='doc_id == "doc_1"')
```

## Distance Metrics

| Metric | Description | Best For |
|--------|-------------|----------|
| **COSINE** | Cosine similarity | Text embeddings (recommended) |
| **L2** | Euclidean distance | Spatial data |
| **IP** | Inner Product | Recommendation systems |

## RAG Integration

```python
# Initialize vector store
vector_store = MilvusLiteStore(
    uri="./knowledge_base.db",
    collection_name="documents",
    dimensions=768,
    distance="COSINE",
)

# Add documents with embeddings
await vector_store.add(documents)

# Search and retrieve for RAG
query_embedding = embedding_model.embed(user_query)
relevant_docs = await vector_store.search(query_embedding=query_embedding, limit=3)

# Use retrieved context
context = "\n".join([doc.metadata.content.text for doc in relevant_docs])
response = agent(f"Context: {context}\n\nQuestion: {user_query}")
```

## Filter Expressions

```python
# Exact match
filter_expr='doc_id == "doc_1"'

# Pattern matching
filter_expr='doc_id like "prefix%"'

# Numeric and logical operators
filter_expr='chunk_id >= 0 and total_chunks > 1'
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

**How do I delete the database?**
Delete the `.db` file specified in the `uri` parameter.

**Is this suitable for production?**
MilvusLite works well for development and small-scale applications. For production at scale, consider Milvus standalone or cluster mode.

## References

- [Milvus Documentation](https://milvus.io/docs)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)
