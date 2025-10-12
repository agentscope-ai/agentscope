```
Module: `src/agentscope/rag/_reader`
Responsibility: Document reader interface and content extraction for RAG workflow.

Key Types: `DocumentReader`, `ContentExtractor`, `MetadataParser`

Key Functions/Methods
- `read_document(file_path, extractor_mode='auto')` — processes various document formats for retrieval
  - Purpose: Handles document parsing, content extraction, and metadata generation for indexing

Related SOP: `docs/rag/SOP.md`