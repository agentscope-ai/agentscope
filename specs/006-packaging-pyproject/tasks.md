---

description: "Migrate easy packaging to pyproject.toml (PEP 621) while preserving full/dev extras"

---

# Tasks: Align packaging to pyproject.toml (keep full/dev extras)

**Input**: `specs/006-packaging-pyproject/spec.md`, `specs/006-packaging-pyproject/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `006-packaging-pyproject`

## Constitution Gates (applies to all tasks)

- No public API/tool schema changes in this feature.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before marking tasks complete.

## Phase 1: Setup (P1)

- [ ] T001 Confirm upstream `main` uses `pyproject.toml` and capture the minimal fields to mirror (name/version/extras).
- [ ] T002 Confirm current easy dependency lists (`setup.py`) and decide which stability pins to adopt from `main` (if any).

---

## Phase 2: Implement Packaging Migration (P1)

- [ ] T003 Add `pyproject.toml` with:
  - `[project]` metadata (name, dynamic version, requires-python, dependencies)
  - `[project.optional-dependencies]` with `full` and `dev` only
  - setuptools find-packages under `src/`
  - include `py.typed` in package-data
- [ ] T004 Remove `setup.py` and ensure no workflows/docs depend on it.
- [ ] T005 Ensure extras policy holds:
  - keys are exactly `full` and `dev`
  - `dev` is a superset of `full`
  - Windows marker for Milvus Lite stays in place

---

## Phase 3: Verification (P1)

- [ ] T006 Run `./.venv/bin/pre-commit run --all-files`.
- [ ] T007 Run `./.venv/bin/python -m ruff check src`.
- [ ] T008 Run `./.venv/bin/python -m pylint -E src`.
- [ ] T009 Run `./.venv/bin/python -m build`.
- [ ] T010 Smoke test install:
  - `./.venv/bin/pip install dist/*.whl`
  - `./.venv/bin/python -c "import agentscope; print(agentscope.__version__)"`
- [ ] T011 Run a focused pytest subset if needed (packaging-only change; optional).
- [ ] T012 Commit with message like: `chore(packaging): migrate to pyproject.toml`.

