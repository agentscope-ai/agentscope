---

description: "Absorb main -> easy ReActAgent L1 fixes"

---

# Tasks: Absorb main ReActAgent L1 fixes

**Input**: `specs/008-reactagent-l1-fixes/spec.md`, `specs/008-reactagent-l1-fixes/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `008-reactagent-l1-fixes`

## Constitution Gates (applies to all tasks)

- If changes touch public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [ ] T001 Confirm target commits exist on local `main`
- [ ] T002 Confirm expected touched file is `src/agentscope/agent/_react_agent.py`

## Phase 2: Implement L1 fixes

- [ ] T003 [US1] Cherry-pick `dd05db2` into `src/agentscope/agent/_react_agent.py`
- [ ] T004 [US2] Cherry-pick `df96805` into `src/agentscope/agent/_react_agent.py`
- [ ] T005 [US3] Cherry-pick `d3c0c1d` into `src/agentscope/agent/_react_agent.py`

## Phase 3: Verification

- [ ] T006 Run `pre-commit run --all-files`
- [ ] T007 Run `./.venv/bin/python -m ruff check src`
- [ ] T008 Run `./.venv/bin/python -m pylint -E src`
- [ ] T009 Run `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
- [ ] T010 Commit with message like: `agent: absorb main ReactAgent L1 fixes`
