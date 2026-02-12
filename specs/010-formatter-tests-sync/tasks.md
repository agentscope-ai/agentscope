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

- [ ] T001 Confirm target commit `19cba5c` exists on local `main`
- [ ] T002 Confirm expected touched files are tests-only under `tests/formatter_*`

## Phase 2: Implement

- [ ] T003 [US1] Cherry-pick `19cba5c` into `easy` (tests-only)

## Phase 3: Verification

- [ ] T004 Run `pre-commit run --all-files`
- [ ] T005 Run `./.venv/bin/python -m ruff check src`
- [ ] T006 Run `./.venv/bin/python -m pylint -E src`
- [ ] T007 Run targeted formatter tests in CI (local optional)
- [ ] T008 Commit spec artifacts + task checkmarks
