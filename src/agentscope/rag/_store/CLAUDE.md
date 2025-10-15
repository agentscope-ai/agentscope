```
Module: `src/agentscope/rag/_store`
Responsibility: Vector storage and document indexing management for RAG retrieval operations.

Key Types: `VectorStore`, `DocumentIndex`, `EmbeddingManager`

Key Functions/Methods
- `index_documents(documents, embeddings)` — builds and maintains searchable document indices
  - Purpose: Coordinates embedding generation, vector storage, and similarity search infrastructure

Related SOP: `docs/rag/SOP.md`