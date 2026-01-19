# MongoDB Vector Store

This example demonstrates how to use **MongoDBStore** for vector storage and semantic search in AgentScope using MongoDB's Vector Search capabilities.
It includes comprehensive test scenarios covering CRUD operations, metadata filtering, document chunking, and distance metrics.

### Quick Start

Install agentscope first, and then the MongoDB dependency:

```bash
pip install pymongo
```

**Important:** Before running the example, you need to set the `MONGODB_HOST`
environment variable with your MongoDB connection string:

```bash
# For local MongoDB
export MONGODB_HOST="mongodb://localhost:27017/?directConnection=true"

# For MongoDB Atlas (replace with your connection string)
# export MONGODB_HOST=${YOUR_MONGODB_HOST}
```

Run the example script, which showcases adding, searching, and deleting in MongoDB vector store:

```bash
python main.py
```

> **Note:** The script connects to MongoDB Atlas or local MongoDB instance. Make sure you have a valid MongoDB connection string.

## Prerequisites

- Confirm your MongoDB instance supports Vector Search functionality
- Valid MongoDB connection string (local or Atlas)

## Usage

### Initialize Store

```python
from agentscope.rag import MongoDBStore

# For MongoDB Atlas
store = MongoDBStore(
    host="mongodb+srv://username:password@cluster.mongodb.net/",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,              # Match your embedding model
    distance="cosine",           # cosine, euclidean, or dotProduct
)

# For local MongoDB
store = MongoDBStore(
    host="mongodb://localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)

# To enable filtering in search, specify filter_fields:
store = MongoDBStore(
    host="mongodb://localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
    filter_fields=["payload.doc_id", "payload.chunk_id"],  # Fields for filtering
)

# No manual initialization needed - everything is automatic!
# Database, collection, and vector search index are created automatically
# when you first call add() or search()
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
results = await store.search(
    query_embedding=[0.15, 0.25, ...],
    limit=5,
    score_threshold=0.9,                                # Optional
    filter={"payload.doc_id": {"$in": ["doc_1", "doc_2"]}},  # Optional filter
)
# Note:
# - To use filter, the field must be declared in filter_fields when creating store
# - MongoDB $vectorSearch filter supports: $gt, $gte, $lt, $lte,
#   $eq, $ne, $in, $nin, $exists, $not (NOT $regex)
```

### Delete

```python
# Delete by document IDs (no initialization needed)
await store.delete(ids=["doc_1", "doc_2"])

# Delete entire collection (use with caution)
await store.delete_collection()

# Delete entire database (use with caution)
await store.delete_database()
```

## Distance Metrics

| Metric | Description | Best For |
|--------|-------------|----------|
| **cosine** | Cosine similarity | Text embeddings (recommended) |
| **euclidean** | Euclidean distance | Spatial data |
| **dotProduct** | Inner Product | Recommendation systems |

## Advanced Usage

### Access Underlying Client

```python
client = store.get_client()
# Use MongoDB client for advanced operations
stats = await client[store.db_name].command("collStats", store.collection_name)
```

### Document Metadata

- `content`: Text content (TextBlock)
- `doc_id`: Unique document identifier
- `chunk_id`: Chunk position (0-indexed)
- `total_chunks`: Total chunks in document

### Vector Search Index

MongoDBStore automatically creates vector search indexes with the following configuration:

```python
{
    "fields": [
        {
            "type": "vector",
            "path": "vector",
            "similarity": "cosine",  # or euclidean, dotProduct
            "numDimensions": 768
        }
    ]
}
```

## Connection Examples

### MongoDB Atlas

```python
store = MongoDBStore(
    host="<YOUR_MONGO_ATLAS_CONNECTION_STRING>",
    db_name="production_db",
    collection_name="documents",
    dimensions=1536,
    distance="cosine",
)
```

### Local MongoDB

#### Without Authentication

```python
store = MongoDBStore(
    host="mongodb://localhost:27017?directConnection=true",
    db_name="local_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)
```

#### With Authentication

```python
store = MongoDBStore(
    host="mongodb://user:pass@localhost:27017/?directConnection=true",
    db_name="test_db",
    collection_name="test_collection",
    dimensions=768,
    distance="cosine",
)
```

## References

- [MongoDB Vector Search Documentation](https://www.mongodb.com/docs/atlas/atlas-search/vector-search/)
- [MongoDB Atlas Documentation](https://www.mongodb.com/docs/atlas/)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)
