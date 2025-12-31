# Feature Specification: Absorb main Tier-A RAG additions (py.typed + MilvusLiteStore + WordReader)

**Feature Branch**: `001-absorb-main-rag-tierA`  
**Base Branch**: `easy` (do not use `main/master` as mainline)  
**Created**: 2025-12-25  
**Status**: Draft  
**Input**: User description: "A1,A2,A3一起，记住要符合easy分支生态进行（cherry-pick 吸收，不做 merge 同步）"

## Clarifications

### Session 2025-12-30

- Q: How should `setup.py` expose new optional deps (extras)? → A: Only extend existing `agentscope[full]` (no new extras keys).
- Q: What is `WordReader` default for image extraction? → A: Default `include_image=False`, and explicitly document requirements/risks when enabling images.
- Q: Should we add a minimal WordReader functionality test (beyond imports)? → A: Yes; add a pytest that generates a `.docx` on the fly and validates `WordReader(include_image=False)` output.
- Q: Should `pymilvus[milvus_lite]` be excluded on Windows via platform markers? → A: Yes; use `platform_system != "Windows"` to avoid Windows install failures.
- Q: Should `agentscope.rag` always export `WordReader` and `MilvusLiteStore` even if optional deps are missing? → A: Yes; import must succeed and missing deps should only fail at runtime usage.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Typed package marker is present (Priority: P1)

As a developer, I want `agentscope` to be recognized as a typed package (PEP 561),
so that type checkers can correctly load inline type hints from the package.

**Why this priority**: Low risk / low effort, and it improves developer experience immediately.

**Independent Test**: `importlib.resources.files("agentscope").joinpath("py.typed").is_file()` is `True`.

**Acceptance Scenarios**:

1. **Given** the repository source tree, **When** I access package resources for `agentscope`,
   **Then** `py.typed` exists under the package directory.
2. **Given** the project packaging configuration, **When** the package is built and installed,
   **Then** `py.typed` is included in the installed package data.

---

### User Story 2 - MilvusLiteStore is available as an optional RAG backend (Priority: P1)

As a developer, I want to use Milvus Lite / Milvus Server as a vector store backend in RAG,
so I can choose Milvus when Qdrant is not desired.

**Why this priority**: Adds a meaningful backend option while remaining mostly additive and optional.

**Independent Test**: `from agentscope.rag import MilvusLiteStore` succeeds, and importing
`agentscope.rag` does not eagerly import `pymilvus`.

**Acceptance Scenarios**:

1. **Given** `pymilvus` is not installed, **When** I `import agentscope.rag`,
   **Then** the import succeeds (no hard failure due to missing optional deps).
2. **Given** `pymilvus` is not installed, **When** I instantiate `MilvusLiteStore(...)`,
   **Then** it raises `ImportError` with a clear install hint: `pip install pymilvus[milvus_lite]`.
3. **Given** `pymilvus` is installed, **When** I use `MilvusLiteStore.add/search/delete`,
   **Then** it behaves as a `VDBStoreBase` implementation.

---

### User Story 3 - WordReader is available as an optional RAG reader (Priority: P2)

As a developer, I want to ingest `.docx` documents into the RAG pipeline,
so I can build knowledge bases from common enterprise document formats.

**Why this priority**: Useful ingestion capability; still optional and additive.

**Independent Test**: `from agentscope.rag import WordReader` succeeds, and importing
`agentscope.rag` does not eagerly import `docx`.

**Acceptance Scenarios**:

1. **Given** `python-docx` is not installed, **When** I `import agentscope.rag`,
   **Then** the import succeeds (no hard failure due to missing optional deps).
2. **Given** `python-docx` is not installed, **When** I call `WordReader()(path)`,
   **Then** it raises `ImportError` with a clear install hint: `pip install python-docx`.
3. **Given** a `.docx` containing paragraphs/tables/images and `include_image=True`,
   **When** I call `WordReader()(path)`,
   **Then** returned `Document.metadata.content` preserves order and uses `TextBlock` / `ImageBlock`.

### Edge Cases

- `Milvus Lite` OS limitation: Windows is not supported (upstream note).
- `WordReader` table rendering: Markdown rendering may be lossy when cells include `\\n`;
  JSON table format should be used for fidelity.
- `WordReader` image extraction default: `include_image=False` by default to avoid unexpected large base64 payloads and multimodal requirements.
- `WordReader(include_image=True)` requires a multimodal embedding model; otherwise downstream
  embedding may fail (out of scope for this feature).
- Sentence splitting note: if `split_by="sentence"` uses NLTK, it is English-focused.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST include `src/agentscope/py.typed` and ensure it is packaged.
- **FR-002**: System MUST provide `MilvusLiteStore` as `agentscope.rag.MilvusLiteStore`.
- **FR-003**: System MUST provide `WordReader` as `agentscope.rag.WordReader`.
- **FR-004**: System MUST NOT require `python-docx` or `pymilvus` for importing `agentscope` or `agentscope.rag`.
- **FR-005**: System MUST only extend existing `setup.py` extras so that installing `agentscope[full]`
  includes `python-docx` and `pymilvus[milvus_lite]; platform_system != "Windows"` (do not add new extras keys).
- **FR-006**: System MUST keep the existing `easy`-specific ecosystems (SubAgent/FS/Search/Web)
  behavior unchanged (no semantic changes).
- **FR-008**: System MUST ensure `from agentscope.rag import WordReader, MilvusLiteStore` succeeds even when
  optional dependencies are missing; missing deps should only raise when the feature is used.
- **FR-007**: System MUST include a minimal `WordReader` functionality test that generates a `.docx`
  at runtime (no binary fixture) and asserts parsed `Document.metadata.content` is a `TextBlock`.

### Key Entities

- **MilvusLiteStore**: A `VDBStoreBase` implementation backed by Milvus Lite/Server via `pymilvus`.
- **WordReader**: A `ReaderBase` implementation that extracts text/table/image blocks from `.docx`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `from agentscope.rag import MilvusLiteStore, WordReader` works in a clean environment.
- **SC-002**: `import agentscope.rag` does not import `docx` or `pymilvus` eagerly (no optional-dep side effects).
- **SC-003**: `./.venv/bin/python -m pytest -q tests/rag_reader_test.py tests/rag_store_test.py` passes.
- **SC-004**: `./.venv/bin/python -m ruff check src` passes (no new warnings).
- **SC-005**: WordReader minimal functionality test passes without requiring external `.docx` fixtures.
- **SC-006**: Installing `.[dev]` on Windows does not fail due to Milvus Lite extras.
