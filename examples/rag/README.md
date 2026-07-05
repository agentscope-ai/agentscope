# RAG Examples

Two library-mode walk-throughs of `agentscope.rag` — no FastAPI service, no manager, no message bus. Each script wires the building blocks (parser, chunker, embedding model, vector store, `KnowledgeBase` handle) by hand so the data flow is visible end-to-end.

| Script | What it shows |
| --- | --- |
| [`index_and_search.py`](./index_and_search.py) | The minimal pipeline: parse → chunk → embed → insert, then `KnowledgeBase.search`. Start here. |
| [`integrate_with_agent.py`](./integrate_with_agent.py) | Attaches the same `KnowledgeBase` to an `Agent` via `RAGMiddleware`, in both `static` (auto-inject) and `agentic` (tool-driven) modes. |

Both examples use an in-memory Qdrant store (`location=":memory:"`) and the DashScope `text-embedding-v4` model, so no external services are required.

## Install

```bash
# From PyPI
uv pip install "agentscope[rag]"

# Or from source (repo root)
uv pip install -e ".[rag]"
```

To use a local persistent Milvus Lite vector store instead of the
in-memory Qdrant store, install the optional extra:

```bash
uv pip install "agentscope[milvuslite]"
# Or from source (repo root)
uv pip install -e ".[milvuslite]"
```

Then replace the vector store construction:

```python
from agentscope.rag import MilvusLiteStore

store = MilvusLiteStore(uri="./rag_demo.db")
```

`integrate_with_agent.py` additionally uses `DashScopeChatModel`, which is already in the base `agentscope` dependencies.

## Run

```bash
export DASHSCOPE_API_KEY=sk-...

python examples/rag/index_and_search.py
python examples/rag/integrate_with_agent.py
```

## Service mode

The two scripts above are library-mode — you drive the pipeline yourself in a single process. For the full service-mode experience (FastAPI endpoints for knowledge base CRUD, document upload, indexing workers, and search), see [`examples/agent_service`](../agent_service) for the backend and [`examples/web_ui`](../web_ui) for the chat-style UI.

