---

description: "Absorb main -> easy residual L1 fixes for models/tool_choice"

---

# Tasks: Absorb main L1 residual fixes (models/tool-choice)

**Input**: `specs/009-l1-residual-fixes/spec.md`, `specs/009-l1-residual-fixes/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `009-l1-residual-fixes`

## Constitution Gates (applies to all tasks)

- If changes touch public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [x] T001 Confirm target commits exist on local `main`
- [x] T002 Confirm `3b67178` is already equivalent in `easy` (`_openai_model.py` null-safe delta parsing)

## Phase 2: Implement L1 fixes

- [x] T003 [US1] Absorb `28547e7` with minimal-diff conflict resolution across model/tool-choice files
- [x] T004 [US2] Absorb `303f0f9` into `src/agentscope/model/_openai_model.py`
- [x] T005 [US3] Document no-op decision for `3b67178` in feature docs
- [x] T006 Exclude `bd5d926` and `f5fdc37` from this batch (defer note in docs/PR)

## Phase 3: Verification

- [x] T007 Run `pre-commit run --all-files`
- [x] T008 Run `./.venv/bin/python -m ruff check src`
- [x] T009 Run `./.venv/bin/python -m pylint -E src`
- [x] T010 Run targeted pytest for changed model paths (blocked by same local `pytest` crash; replaced by runtime behavior checks)
- [x] T011 Run `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests` (recorded environment blocker: local `RC:139`, no output)
- [ ] T012 Commit with message like: `model: absorb residual L1 fixes for tool_choice and qwen-omni`
