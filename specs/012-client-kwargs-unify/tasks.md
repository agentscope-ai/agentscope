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

- [ ] T001 Confirm target commit `141b2c4` exists on local `main`
- [ ] T002 Confirm `client_args` contract remains unchanged for OpenAI/Gemini/Anthropic

## Phase 2: Implement

- [ ] T003 [US1] Absorb DashScope tolerance for extra kwargs
- [ ] T004 [US2] Absorb Ollama `client_kwargs` + `generate_kwargs` support

## Phase 3: Verification

- [ ] T005 Run `pre-commit run --all-files`
- [ ] T006 Run `./.venv/bin/python -m ruff check src`
- [ ] T007 Run `./.venv/bin/python -m pylint -E src`
- [ ] T008 Run focused runtime checks for signature binding and kwargs merge
- [ ] T009 Run pytest in CI (local optional; record environment blocker if `RC:139`)
- [ ] T010 Commit task checkmarks
