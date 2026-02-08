---

description: "Absorb main -> easy low-risk pointfixes (L1)"

---

# Tasks: Absorb main low-risk pointfixes (L1)

**Input**: `specs/004-main-pointfixes/spec.md`, `specs/004-main-pointfixes/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `004-main-pointfixes`

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / tool schemas: update the relevant
  `docs/**/SOP.md`, add `todo.md` acceptance items, and include field-set-equality
  contract tests.
- Run quality gates in `./.venv` (`pre-commit`, `ruff`, `pylint`, `pytest`) before
  marking tasks complete.

## Phase 1: Setup

- [ ] T001 Confirm target upstream commits exist on local `main` (no fetch)
- [ ] T002 Confirm expected touched files match the plan

---

## Phase 2: Implement Fix Bundle (P1)

- [ ] T003 [P] [US1] Absorb OpenAI delta None fix in `src/agentscope/model/_openai_model.py`
- [ ] T004 [P] [US1] Absorb DeepSeek reasoning_content fix in `src/agentscope/formatter/_deepseek_formatter.py`
- [ ] T005 [P] [US1] Absorb DashScope embedding dimension fix in `src/agentscope/embedding/_dashscope_embedding.py`
- [ ] T006 [P] [US1] Absorb Windows utf-8 env fix in `src/agentscope/tool/_coding/_python.py`
- [ ] T007 [US1] Port `_json_loads_with_repair` dict-only repair fix in `src/agentscope/_utils/_common.py`

---

## Phase 3: Verification + Clean

- [ ] T008 Run `pre-commit run --all-files`
- [ ] T009 Run `./.venv/bin/python -m ruff check src`
- [ ] T010 Run `./.venv/bin/python -m pylint -E src`
- [ ] T011 Run `./.venv/bin/python -m pytest -q`
- [ ] T012 Commit with message like: `chore: absorb main L1 pointfixes`
