```
Module: `src/agentscope/embedding`
Responsibility: Embedding models and vector operations for agentic RAG.
Key Types: `EmbeddingModel`, `EmbeddingOutput`

Key Functions/Methods
- `__call__(documents, **kwargs)` — processes text inputs into vector embeddings
  - Purpose: Converts documents/tokens into numerical representations for semantic search
  - Inputs: Text documents or tokens
  - Returns: Numerical embedding vectors
  - Side‑effects: Network calls to embedding services
  - References: `src/agentscope/embedding/__init__.py`

Related SOP: `docs/embedding_usage.md`