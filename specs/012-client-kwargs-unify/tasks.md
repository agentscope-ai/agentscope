---

description: "Absorb main -> easy model client_kwargs unification (DashScope/Ollama)"

---

# Tasks: Absorb model client_kwargs unification (DashScope/Ollama)

**Input**: `specs/012-client-kwargs-unify/spec.md`, `specs/012-client-kwargs-unify/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `012-client-kwargs-unify`

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / schemas: stop and update SOP + tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before completion.

## Phase 1: Setup

- [x] T001 Confirm target commit `141b2c4` exists on local `main`
- [x] T002 Confirm `client_args` contract remains unchanged for OpenAI/Gemini/Anthropic

## Phase 2: Implement

- [x] T003 [US1] Absorb DashScope tolerance for extra kwargs
- [x] T004 [US2] Absorb Ollama `client_kwargs` + `generate_kwargs` support

## Phase 3: Verification

- [x] T005 Run `pre-commit run --all-files`
- [x] T006 Run `./.venv/bin/python -m ruff check src`
- [x] T007 Run `./.venv/bin/python -m pylint -E src`
- [x] T008 Run focused runtime checks for signature binding and kwargs merge
- [x] T009 Run pytest in CI (local blocked: `pytest RC:139`, no output; CI is final oracle)
- [x] T010 Commit task checkmarks
