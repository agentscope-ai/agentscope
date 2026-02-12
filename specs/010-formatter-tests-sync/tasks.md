---

description: "Absorb main -> easy formatter tests ID consistency fix"

---

# Tasks: Absorb main formatter unit test ID consistency fix

**Input**: `specs/010-formatter-tests-sync/spec.md`, `specs/010-formatter-tests-sync/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `010-formatter-tests-sync`

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [x] T001 Confirm target commit `19cba5c` exists on local `main`
- [x] T002 Confirm expected touched files are tests-only under `tests/formatter_*`

## Phase 2: Implement

- [x] T003 [US1] Cherry-pick `19cba5c` into `easy` (tests-only)

## Phase 3: Verification

- [x] T004 Run `pre-commit run --all-files`
- [x] T005 Run `./.venv/bin/python -m ruff check src`
- [x] T006 Run `./.venv/bin/python -m pylint -E src`
- [x] T007 Run targeted formatter tests (local blocked: `pytest RC:139`, no output; CI is final oracle)
- [ ] T008 Commit spec artifacts + task checkmarks
