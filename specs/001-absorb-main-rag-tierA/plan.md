# Implementation Plan: Absorb main Tier-A RAG additions (py.typed + MilvusLiteStore + WordReader)

**Branch**: `001-absorb-main-rag-tierA` | **Date**: 2025-12-25 | **Spec**: `specs/001-absorb-main-rag-tierA/spec.md`  
**Input**: Feature specification from `specs/001-absorb-main-rag-tierA/spec.md`

## Summary

Cherry-pick (selective absorption) from local `main` into `easy` for three low-risk additions:

1. `py.typed` marker for typed-package tooling.
2. Milvus Lite vector store backend (`MilvusLiteStore`) as an optional dependency.
3. Word `.docx` reader (`WordReader`) as an optional dependency.

Key constraint: align with the `easy` ecosystem by keeping SubAgent/Filesystem/Search/Web behavior
unchanged, and ensuring optional deps do not break import-time behavior.

## Technical Context

**Language/Version**: Python (repo declares `python_requires>=3.10`)  
**Primary Dependencies**: Packaging via `setup.py`; tests via `pytest`  
**Storage**: N/A (library code)  
**Testing**: `pytest` (use `./.venv/bin/python`)  
**Target Platform**: cross-platform, with upstream note that Milvus Lite is not supported on Windows  
**Project Type**: single package (`src/agentscope`, `tests/`)  
**Constraints**:
- No hard import dependency on `python-docx` or `pymilvus` at `agentscope` import time
- Exports MUST work via `from agentscope.rag import ...`
- Update only what is necessary for this absorption; avoid touching unrelated modules

## Constitution Check

*GATE: Must pass before implementation. Re-check after code changes.*

- [ ] **Branch mainline**: base branch is `easy`; PR target is `easy`; do not develop on `main/master`.
- [ ] **Docs-first**: update relevant `docs/**/SOP.md` and root `todo.md` acceptance items before implementation.
- [ ] **Zero-deviation contract**: any tool/schema surface change has field-set-equality tests (no extra/hidden fields).
- [ ] **Security boundaries**: no secrets; E2E scripts auto-load `.env` and fail-fast on missing keys.
- [ ] **Quality gates**: run checks in `./.venv` (`ruff check src` + relevant `pytest`) with zero warnings.

## Project Structure

### Documentation (this feature)

```text
specs/001-absorb-main-rag-tierA/
├── spec.md              # This file
├── plan.md              # This file
└── tasks.md             # Phase 2 output (task list for implementation)
```

### Source Code (repository root)

```text
src/agentscope/
├── py.typed
└── rag/
    ├── __init__.py
    ├── _reader/
    │   ├── __init__.py
    │   └── _word_reader.py
    └── _store/
        ├── __init__.py
        └── _milvuslite_store.py

setup.py
tests/
├── rag_reader_test.py
└── rag_store_test.py
```

## Implementation Notes (Design Decisions)

- **Optional dependency safety**:
  - `WordReader` MUST not import `python-docx` at module import time; import should happen only
    at runtime when reading/processing.
  - `MilvusLiteStore` MUST only import `pymilvus` when instantiated, and raise a helpful `ImportError`
    with install instructions if missing.
- **Exports**:
  - Update `src/agentscope/rag/_reader/__init__.py`, `src/agentscope/rag/_store/__init__.py`,
    and `src/agentscope/rag/__init__.py` so that `from agentscope.rag import WordReader, MilvusLiteStore`
    works.
- **Packaging**:
  - Update `setup.py` extras so `agentscope[full]` includes `python-docx` and `pymilvus[milvus_lite]`.
  - Use `platform_system != "Windows"` marker for `pymilvus[milvus_lite]` to keep Windows installs green.
  - Ensure `py.typed` is included in package data (otherwise type checkers may not detect it after install).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
