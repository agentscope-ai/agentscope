```
Module: `src/agentscope/rag`
Responsibility: Agentic Retrieval Augmented Generation framework supporting multimodal RAG.
Key Types: `RAGRetriever`, `RAGGenerator`, `QueryProcessor`

Key Functions/Methods
- `query_retrieve(question, context=None)`
  - Purpose: Performs semantic search and context retrieval for agent reasoning

Call Graph
- `Agent.query()` → `RAGRetriever.search()` → `RAGGenerator.generate_answer()`
  - References: `src/agentscope/rag/__init__.py`

Related SOP: `docs/rag_workflow.md`