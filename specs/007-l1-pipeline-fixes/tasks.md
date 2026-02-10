---

description: "Absorb main -> easy L1 pipeline fixes (MsgHub, NLTK quiet, DashScope MM api_key, stream exceptions)"

---

# Tasks: Absorb main L1 pipeline fixes

**Input**: `specs/007-l1-pipeline-fixes/spec.md`, `specs/007-l1-pipeline-fixes/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `007-l1-pipeline-fixes`

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / tool schemas: update the relevant
  `docs/**/SOP.md`, add `todo.md` acceptance items, and include field-set-equality
  contract tests.
- Run quality gates in `./.venv` (pre-commit, ruff, pylint, pytest) before
  marking tasks complete.

## Phase 1: Setup

- [ ] T001 Confirm target upstream commits exist on local `main`
- [ ] T002 Confirm expected touched files match the plan (src/agentscope/pipeline/_functional.py, src/agentscope/pipeline/_msghub.py, src/agentscope/rag/_reader/_text_reader.py, src/agentscope/embedding/_dashscope_multimodal_embedding.py, tests/pipeline_test.py)

---

## Phase 2: User Story 1 - Pipeline errors are not silently swallowed (Priority: P1)

**Goal**: Streaming pipeline surfaces task exceptions reliably.

**Independent Test**: `tests/pipeline_test.py` covers "print then raise" and asserts the exception is raised.

- [ ] T003 [US1] Apply upstream fix in `src/agentscope/pipeline/_functional.py` to raise task exception after streaming ends
- [ ] T004 [US1] Add pipeline test coverage in `tests/pipeline_test.py` for error after printing

---

## Phase 3: User Story 2 - RAG sentence splitting is less noisy (Priority: P2)

**Goal**: NLTK downloads run quietly.

**Independent Test**: Existing RAG reader tests continue to pass.

- [ ] T005 [US2] Apply quiet NLTK downloads in `src/agentscope/rag/_reader/_text_reader.py`

---

## Phase 4: User Story 3 - DashScope multimodal embedding uses configured credentials (Priority: P2)

**Goal**: Provider call includes configured API key.

**Independent Test**: Provider call path includes `api_key` argument.

- [ ] T006 [US3] Apply api_key injection in `src/agentscope/embedding/_dashscope_multimodal_embedding.py`

---

## Phase 5: User Story 4 - MsgHub accepts participant sequences (Priority: P3)

**Goal**: MsgHub accepts any participant sequence.

**Independent Test**: Add a small runtime construction check using a tuple.

- [ ] T007 [US4] Apply Sequence typing + internal list conversion in `src/agentscope/pipeline/_msghub.py`
- [ ] T008 [US4] Add a basic construction test in `tests/pipeline_test.py` to ensure tuples are accepted by MsgHub

---

## Phase 6: Verification + Commit

- [ ] T009 Run `pre-commit run --all-files`
- [ ] T010 Run `./.venv/bin/python -m ruff check src`
- [ ] T011 Run `./.venv/bin/python -m pylint -E src`
- [ ] T012 Run `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
- [ ] T013 Commit with message like: `pipeline: absorb main L1 fixes`
