---

description: "Absorb main -> easy mem0 graphstore compatibility fix"

---

# Tasks: Absorb mem0 graphstore compatibility fix (L1)

**Input**: `specs/011-mem0-graphstore-fix/spec.md`, `specs/011-mem0-graphstore-fix/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `011-mem0-graphstore-fix`

## Constitution Gates (applies to all tasks)

- If changes touch public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [ ] T001 Confirm target commit `233915d` exists on local `main`
- [ ] T002 Confirm expected touched files are mem0 integration + example only

## Phase 2: Implement

- [ ] T003 [US1] Cherry-pick `233915d` and ensure relations are included in retrieval outputs
- [ ] T004 [US2] Ensure AgentScopeLLM returns mem0-compatible structured output when tools are provided

## Phase 3: Verification

- [ ] T005 Run `pre-commit run --all-files`
- [ ] T006 Run `./.venv/bin/python -m ruff check src`
- [ ] T007 Run `./.venv/bin/python -m pylint -E src`
- [ ] T008 Run focused runtime checks for relations formatting and tool-call parsing
- [ ] T009 Run pytest in CI (local optional; record environment blocker if `RC:139`)
- [ ] T010 Commit task checkmarks
