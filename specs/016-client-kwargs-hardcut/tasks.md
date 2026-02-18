# Tasks: Hard-cut chat model client kwargs naming

**Input**: `specs/016-client-kwargs-hardcut/spec.md`, `specs/016-client-kwargs-hardcut/plan.md`

## Phase 1: Setup

- [x] T001 Create branch `016-client-kwargs-hardcut` from `easy`
- [x] T002 Confirm `client_args` references and impacted files via code search

## Phase 2: Implementation

- [x] T003 [US1] Rename constructor kwarg to `client_kwargs` in `src/agentscope/model/_openai_model.py`
- [x] T004 [US1] Rename constructor kwarg to `client_kwargs` in `src/agentscope/model/_gemini_model.py`
- [x] T005 [US1] Rename constructor kwarg to `client_kwargs` in `src/agentscope/model/_anthropic_model.py`
- [x] T006 [US1] Update unit tests in `tests/model_openai_test.py`, `tests/model_gemini_test.py`, `tests/model_anthropic_test.py`
- [x] T007 [US2] Update tutorial snippets in `docs/tutorial/en/src/task_model.py` and `docs/tutorial/zh_CN/src/task_model.py`
- [x] T008 [US2] Update examples in `examples/agent/voice_agent/main.py`, `examples/agent_search_subagent/e2e_direct.py`, `examples/agent_search_subagent/e2e_tool_mode.py`, `examples/filesystem_agent/main.py`
- [x] T009 [US3] Update contract wording in `docs/model/SOP.md`

## Phase 3: Verification

- [x] T010 Run global search to confirm no `client_args` remains under `src tests docs examples`
- [x] T011 Run scoped Ruff checks on touched files (full `src/tests/docs/examples` contains pre-existing tutorial lint violations unrelated to this feature)
- [x] T012 Run `./.venv/bin/python -m pylint -E src`
- [x] T013 Run focused runtime tests with unittest fallback (`pytest` crashes locally in this environment)

## Phase 4: Closeout

- [x] T014 Update checklist evidence in `specs/016-client-kwargs-hardcut/checklists/requirements.md`
