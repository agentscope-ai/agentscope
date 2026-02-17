# Implementation Plan: Absorb model client_kwargs unification (DashScope/Ollama)

**Branch**: `012-client-kwargs-unify` | **Date**: 2026-02-13 | **Spec**: specs/012-client-kwargs-unify/spec.md
**Input**: Feature specification from `/specs/012-client-kwargs-unify/spec.md`

## Summary

Absorb a scoped upstream L1 fix from local `main` into `easy`:

- `141b2c4` unifies `client_kwargs` behavior for Ollama and tolerates extra
  kwargs in DashScope.

In easy, we intentionally preserve `client_args` as the contract for
OpenAI/Gemini/Anthropic to stay aligned with `docs/model/SOP.md` and tutorials.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Testing**: pre-commit + ruff + pylint + pytest (CI)
**Target Platform**: GitHub Actions
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Constraints**: minimal diff; preserve docs contract; avoid L3 refactors

## Constitution Check

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface/schema change requiring SOP edits.
- [x] **Zero-deviation contract**: no tool/schema surface change.
- [x] **Security boundaries**: no secret handling or boundary changes.
- [x] **Quality gates**: pre-commit + ruff + pylint + pytest (CI) must pass.

## Upstream Commit Target (local main)

- `141b2c4` fix(model): unify `client_kwargs` in ollama and dashscope chat model
  classes

## Project Structure

### Documentation (this feature)

```text
specs/012-client-kwargs-unify/
├── spec.md
├── plan.md
├── tasks.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── checklists/
```

### Source Code (repository root)

```text
src/agentscope/model/_dashscope_model.py
src/agentscope/model/_ollama_model.py
```
