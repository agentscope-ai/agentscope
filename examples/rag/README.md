# RAG Examples

Two library-mode walk-throughs of `agentscope.rag` — no FastAPI service, no manager, no message bus. Each script wires the building blocks (parser, chunker, embedding model, vector store, `KnowledgeBase` handle) by hand so the data flow is visible end-to-end.

| Script | What it shows |
| --- | --- |
| [`index_and_search.py`](./index_and_search.py) | The minimal pipeline: parse → chunk → embed → insert, then `KnowledgeBase.search`. Start here. |
| [`integrate_with_agent.py`](./integrate_with_agent.py) | Attaches the same `KnowledgeBase` to an `Agent` via `RAGMiddleware`, in both `static` (auto-inject) and `agentic` (tool-driven) modes. |

Both examples use an in-memory Qdrant store (`location=":memory:"`) and the DashScope `text-embedding-v4` model, so no external services are required. The sections below show how to swap in Milvus Lite, MongoDB, or Elasticsearch instead; those backends need additional setup.

## Install

```bash
# From PyPI
uv pip install "agentscope[rag]"

# Or from source (repo root)
uv pip install -e ".[rag]"
```

### Milvus Lite (local persistence)

To use a local persistent Milvus Lite vector store instead of the
in-memory Qdrant store, install the optional extra:

```bash
uv pip install "agentscope[vdb-milvus]"
# Or from source (repo root)
uv pip install -e ".[vdb-milvus]"
```

Then replace the vector store construction in `index_and_search.py`
and/or `integrate_with_agent.py`:

```python
from agentscope.rag import MilvusLiteStore

store = MilvusLiteStore(uri="./rag_demo.db")
```

### MongoDB Vector Search

To use MongoDB as the vector backend instead of the in-memory Qdrant
store — useful when your team already runs MongoDB as the primary data
store and wants to avoid maintaining a separate vector database — install
the optional extra:

```bash
uv pip install "agentscope[vdb-mongodb]"
# Or from source (repo root)
uv pip install -e ".[vdb-mongodb]"
```

**Prerequisites**

- A MongoDB deployment with [Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/) enabled:
  - **MongoDB Atlas** — create a cluster and enable Vector Search on the target database; or
  - **Self-hosted** — MongoDB 7.0+ replica set with Vector Search enabled.
- A connection URI available in the environment (do not hard-code credentials):

```bash
export MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority"
# Self-hosted example:
# export MONGODB_URI="mongodb://localhost:27017"
```

Then replace the vector store construction in `index_and_search.py`
and/or `integrate_with_agent.py`:

```python
import os

from agentscope.rag import MongoDBStore

store = MongoDBStore(
    uri=os.environ["MONGODB_URI"],
    database="agentscope_rag",
    # Declare every field you plan to filter on in search().
    # Required for metadata_filter; defaults to ["document_id"] only.
    filter_fields=[
        "document_id",
        # "chunk.metadata.tenant_id",  # uncomment if you use metadata_filter
    ],
)

# MongoDBStore is also an async context manager — same as QdrantStore.
async with store:
    knowledge = KnowledgeBase(
        name="demo-kb",
        description="A toy corpus on cats and AgentScope.",
        embedding_model=embedding_model,
        vector_store=store,
        collection=COLLECTION,
    )
    ...
```

**Notes**

- The examples use DashScope `text-embedding-v4` with `dimensions=1024`.
  `MongoDBStore.create_collection` is called automatically on the first
  index operation with that dimension — keep the embedding model and
  index dimensions aligned.
- Unlike Qdrant `:memory:` or Milvus Lite (local `.db` file), MongoDB is
  an external service; you must have a reachable cluster before running
  the scripts.
- If `search(..., metadata_filter={...})` returns no results or errors,
  ensure each metadata key is listed in `filter_fields` as
  `chunk.metadata.<key>` when constructing `MongoDBStore`.
- For the full FastAPI RAG service, pass the same `MongoDBStore` instance
  to `CollectionPerKbManager(storage=..., vector_store=...)` in
  `examples/agent_service/main.py` (the default there uses in-memory Qdrant
  for zero-setup demos).

### Elasticsearch

To use Elasticsearch as the vector backend, install the optional async
client extra:

```bash
uv pip install "agentscope[vdb-elasticsearch]"
# Or from source (repo root)
uv pip install -e ".[vdb-elasticsearch]"
```

**Prerequisites**

- A reachable Elasticsearch 8.12+ deployment. The AgentScope extra uses
  the official Elasticsearch 8.x Python client, which is compatible with
  Elasticsearch 8.x and 9.x.
- An Elasticsearch URL available in the environment. Keep credentials and
  TLS settings outside source code.

For local development, the following starts a single-node Elasticsearch
instance with security disabled:

```bash
docker run --rm --name agentscope-elasticsearch \
  -p 9200:9200 \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
  docker.elastic.co/elasticsearch/elasticsearch:8.19.3

export ELASTICSEARCH_URL="http://localhost:9200"
```

Do not disable security in shared or production environments. For an
authenticated HTTPS deployment, also export credentials and the CA
certificate path, then pass them through `client_kwargs` as shown below.

Replace the vector store construction in `index_and_search.py` and/or
`integrate_with_agent.py`:

```python
import os

from agentscope.rag import ElasticsearchStore

client_kwargs = {}
if os.getenv("ELASTICSEARCH_API_KEY"):
    client_kwargs["api_key"] = os.environ["ELASTICSEARCH_API_KEY"]
if os.getenv("ELASTICSEARCH_CA_CERTS"):
    client_kwargs["ca_certs"] = os.environ["ELASTICSEARCH_CA_CERTS"]

store = ElasticsearchStore(
    hosts=os.environ["ELASTICSEARCH_URL"],
    # Number of HNSW candidates considered per shard. Higher values can
    # improve recall at the cost of additional search work.
    num_candidates=100,
    # Use False for higher write throughput during large imports when
    # immediate search visibility is not required.
    refresh="wait_for",
    client_kwargs=client_kwargs,
)

# ElasticsearchStore implements the same async context-manager and
# VectorStoreBase contracts as QdrantStore.
async with store:
    knowledge = KnowledgeBase(
        name="demo-kb",
        description="A toy corpus on cats and AgentScope.",
        embedding_model=embedding_model,
        vector_store=store,
        collection=COLLECTION,
    )
    ...
```

For username/password authentication, configure `basic_auth` instead of an
API key:

```python
store = ElasticsearchStore(
    hosts=os.environ["ELASTICSEARCH_URL"],
    client_kwargs={
        "basic_auth": (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ["ELASTICSEARCH_PASSWORD"],
        ),
        "ca_certs": os.environ["ELASTICSEARCH_CA_CERTS"],
    },
)
```

**Notes**

- Each knowledge base collection maps to a separate Elasticsearch index.
- Collections use an indexed `dense_vector` field with cosine similarity.
  Keep the embedding model dimensions unchanged after an index is created.
- Chunk metadata is stored in an Elasticsearch `flattened` field. Any
  top-level metadata key can therefore be used with
  `search(..., metadata_filter={"tenant_id": "bank-a"})`; no filter-field
  declaration is required.
- Writes use a stable ID derived from `document_id` and `chunk_index`, so
  retrying the same indexing operation replaces existing chunks instead of
  creating duplicates.
- Writes default to `refresh="wait_for"`, making indexed chunks searchable
  before the operation returns. For large imports, set `refresh=False` and
  allow Elasticsearch's normal refresh interval (or an explicit index
  refresh) to make changes visible with higher throughput.
- Elasticsearch transforms cosine similarity into a positive `_score`.
  `ElasticsearchStore` converts it back to raw cosine similarity so score
  thresholds behave consistently with the other vector backends.
- For service mode, construct
  `CollectionPerKbManager(storage=storage, vector_store=store)` and pass it
  to `create_app(knowledge_base_manager=...)`.

### Choosing a vector backend

| | Qdrant (default) | Milvus Lite | MongoDB | Elasticsearch |
| --- | --- | --- | --- | --- |
| Install extra | `agentscope[rag]` | `agentscope[vdb-milvus]` | `agentscope[vdb-mongodb]` | `agentscope[vdb-elasticsearch]` |
| External service | No | No | Yes | Yes |
| Persistence | No (`:memory:`) | Yes (local `.db`) | Yes (server) | Yes (server) |
| Best for | Quick start / tests | Local dev with persistence | Teams already on MongoDB | Teams already on Elastic or needing distributed kNN search |

`integrate_with_agent.py` additionally uses `DashScopeChatModel`, which is already in the base `agentscope` dependencies.

## Run

```bash
export DASHSCOPE_API_KEY=sk-...

python examples/rag/index_and_search.py
python examples/rag/integrate_with_agent.py
```

When using MongoDB, also export `MONGODB_URI` before running. When using
Elasticsearch, export `ELASTICSEARCH_URL` and any required authentication or
TLS environment variables.

## Service mode

The two scripts above are library-mode — you drive the pipeline yourself in a single process. For the full service-mode experience (FastAPI endpoints for knowledge base CRUD, document upload, indexing workers, and search), see [`examples/agent_service`](../agent_service) for the backend and [`examples/web_ui`](../web_ui) for the chat-style UI.
