---

description: "Absorb main -> easy plan notebook + word reader bugfixes (58a4858)"

---

# Tasks: Absorb plan notebook and word reader bugfixes (58a4858)

**Input**: `specs/013-plan-wordreader-fixes/spec.md`, `specs/013-plan-wordreader-fixes/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `013-plan-wordreader-fixes`

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [x] T001 Confirm target commit `58a4858` exists on local `main`
- [x] T002 Confirm `easy` does not already contain `58a4858`
- [x] T003 Create speckit docs under `specs/013-plan-wordreader-fixes/`

## Phase 2: Implement

- [x] T004 [US1] Absorb plan storage/model/notebook fixes from `58a4858`
- [x] T005 [US3] Absorb word reader import/type handling fixes from `58a4858`
- [x] T006 [US1] Align `tests/plan_test.py` with serialization and state updates

## Phase 3: Verification

- [x] T007 Run `pre-commit run --all-files`
- [x] T008 Run `./.venv/bin/python -m ruff check src`
- [x] T009 Run `./.venv/bin/python -m pylint -E src`
- [x] T010 Run focused pytest: `./.venv/bin/python -m pytest tests/plan_test.py` (local blocked: `RC:139`, no pytest output)
- [x] T011 Run broader pytest or record local blocker; CI is final oracle
- [x] T012 Update task checkmarks to reflect completed work
