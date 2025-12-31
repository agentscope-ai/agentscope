---

description: "Absorb main Tier-A RAG additions into easy (py.typed + MilvusLiteStore + WordReader)"

---

# Tasks: Absorb main Tier-A RAG additions (py.typed + MilvusLiteStore + WordReader)

**Input**: `specs/001-absorb-main-rag-tierA/spec.md`, `specs/001-absorb-main-rag-tierA/plan.md`  
**Base Branch**: `easy` (do not use `main/master` as mainline)  
**Branch**: `001-absorb-main-rag-tierA`

## Constitution Gates (applies to all tasks)

- Run quality gates in `./.venv` (e.g. `ruff check src` + relevant `pytest`) before marking tasks complete.
- Docs-first for this feature: update `docs/rag/SOP.md` and root `todo.md` before implementation.
- If a task unintentionally touches tool schemas, stop and add required contract gates and tests.

## Phase 1: Setup (Shared)

- [ ] T001 Confirm local `main` is up-to-date and available for cherry-pick sources (no fetch required)
- [ ] T002 Identify upstream source files and commits for A1/A2/A3 (record in PR description)
- [ ] T003 Update `docs/rag/SOP.md` to include `MilvusLiteStore` and `WordReader` (document `include_image=False` default)
- [ ] T004 Update root `todo.md` with acceptance items for A1/A2/A3 (exports, optional-dep behavior, extras policy, Windows marker)

---

## Phase 2: User Story 1 - Typed package marker is present (P1)

**Independent Test**: `importlib.resources` sees `agentscope/py.typed`.

- [ ] T010 [US1] Add typed marker file `src/agentscope/py.typed`
- [ ] T011 [US1] Ensure packaging includes `py.typed` by updating `setup.py` (`package_data` / include rules)
- [ ] T012 [US1] Add/adjust test to assert `py.typed` exists via `importlib.resources` (e.g., in `tests/`)

---

## Phase 3: User Story 2 - MilvusLiteStore optional backend (P1)

**Independent Test**: `from agentscope.rag import MilvusLiteStore` works; importing `agentscope.rag`
does not import `pymilvus` eagerly.

- [ ] T020 [US2] Add `src/agentscope/rag/_store/_milvuslite_store.py` (from local `main`, minimal adaptation)
- [ ] T021 [US2] Export `MilvusLiteStore` in `src/agentscope/rag/_store/__init__.py`
- [ ] T022 [US2] Export `MilvusLiteStore` in `src/agentscope/rag/__init__.py`
- [ ] T023 [US2] Update `setup.py` extras to include `pymilvus[milvus_lite]` (NOT in minimal requires)
- [ ] T023a [US2] Ensure `pymilvus[milvus_lite]` uses marker `platform_system != "Windows"` (keep Windows install green)
- [ ] T024 [US2] Add a test that `import agentscope.rag` does not eagerly import `pymilvus`
  (assert `"pymilvus" not in sys.modules` after import)
- [ ] T025 [US2] Add a test that `MilvusLiteStore` is a `VDBStoreBase` subclass (no runtime connection required)

---

## Phase 4: User Story 3 - WordReader optional reader (P2)

**Independent Test**: `from agentscope.rag import WordReader` works; importing `agentscope.rag`
does not import `docx` eagerly.

- [ ] T030 [US3] Add `src/agentscope/rag/_reader/_word_reader.py` (from local `main`, minimal adaptation)
- [ ] T031 [US3] Export `WordReader` in `src/agentscope/rag/_reader/__init__.py`
- [ ] T032 [US3] Export `WordReader` in `src/agentscope/rag/__init__.py`
- [ ] T033 [US3] Update `setup.py` extras to include `python-docx` (NOT in minimal requires)
- [ ] T034 [US3] Add a test that `import agentscope.rag` does not eagerly import `docx`
  (assert `"docx" not in sys.modules` after import)
- [ ] T035 [US3] Add minimal WordReader functionality test: pytest generates a `.docx` at runtime (no binary fixture),
  runs `WordReader(include_image=False)`, and asserts parsed `Document.metadata.content` is a `TextBlock`

---

## Phase 5: Quality Gates + Commit

- [ ] T040 Run `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/rag_reader_test.py tests/rag_store_test.py`
- [ ] T041 Run `./.venv/bin/python -m ruff check src`
- [ ] T042 Commit with message like: `rag: absorb main tier-A additions (py.typed + milvus + word reader)`
