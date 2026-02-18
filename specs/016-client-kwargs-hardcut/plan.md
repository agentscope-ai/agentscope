# Implementation Plan: Hard-cut chat model client kwargs naming

**Branch**: `016-client-kwargs-hardcut` | **Date**: 2026-02-19 | **Spec**: `specs/016-client-kwargs-hardcut/spec.md`

## Summary

Apply a focused hard-cut rename from `client_args` to `client_kwargs` for
OpenAI/Gemini/Anthropic chat models on `easy`, then align tests, examples,
tutorial docs, and model SOP to the same contract.

## Technical Context

- **Language/Version**: Python 3.10+
- **Primary Scope**: `src/agentscope/model`, `tests/`, `examples/`, `docs/`
- **Quality Gates**: `ruff`, `pylint -E`, focused `pytest`
- **Constraints**: Minimal surface-area change; avoid unrelated refactor

## Constitution Check

- [x] Base branch remains `easy`
- [x] SOP contract remains the authority and is updated with code
- [x] Change is scoped to a single behavioral axis (constructor kwarg naming)
- [x] Verification includes lint + focused runtime tests

## Change Scope

- Runtime constructors:
  - `src/agentscope/model/_openai_model.py`
  - `src/agentscope/model/_gemini_model.py`
  - `src/agentscope/model/_anthropic_model.py`
- Tests:
  - `tests/model_openai_test.py`
  - `tests/model_gemini_test.py`
  - `tests/model_anthropic_test.py`
- Docs/examples:
  - `docs/tutorial/en/src/task_model.py`
  - `docs/tutorial/zh_CN/src/task_model.py`
  - `docs/model/SOP.md`
  - `examples/agent/voice_agent/main.py`
  - `examples/agent_search_subagent/e2e_direct.py`
  - `examples/agent_search_subagent/e2e_tool_mode.py`
  - `examples/filesystem_agent/main.py`

## Execution Steps

1. Rename constructor parameter name in target chat models.
2. Update test fixtures and assertions to use `client_kwargs`.
3. Update docs/tutorial/example call sites.
4. Validate with search + lint + focused tests.
5. Record evidence in tasks/checklist.
