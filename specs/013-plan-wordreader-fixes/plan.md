# Implementation Plan: Absorb plan notebook and word reader bugfixes (58a4858)

**Branch**: `013-plan-wordreader-fixes` | **Date**: 2026-02-16 | **Spec**: specs/013-plan-wordreader-fixes/spec.md
**Input**: Feature specification from `/specs/013-plan-wordreader-fixes/spec.md`

## Summary

Absorb a scoped upstream medium-risk fix from local `main` into `easy`:

- `58a4858` fix(plan, rag): fix the bug in the word reader and plan notebook
  class.

This batch keeps minimal diff boundaries and avoids formatter/media high-risk
refactors.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Primary Dependencies**: pydantic, shortuuid, python-docx, repo runtime stack
**Storage**: In-memory state module serialization for plan notebook
**Testing**: pre-commit + ruff + pylint + pytest (CI)
**Target Platform**: GitHub Actions (Ubuntu + Python matrix)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Performance Goals**: no measurable regression in plan or reader paths
**Constraints**: minimal diff; preserve easy-only behavior; no L3 refactor
**Scale/Scope**: 5 source/test files from upstream + specs for this batch

## Constitution Check

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no SOP schema/interface contract change in this batch.
- [x] **Zero-deviation contract**: no external tool/schema surface expansion.
- [x] **Security boundaries**: no secret/I/O boundary change.
- [x] **Quality gates**: pre-commit + ruff + pylint + targeted pytest/CI.

## Upstream Commit Target (local main)

- `58a4858` fix(plan, rag): fix the bug in the word reader and plan notebook
  class

## Project Structure

### Documentation (this feature)

```text
specs/013-plan-wordreader-fixes/
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
src/agentscope/plan/_in_memory_storage.py
src/agentscope/plan/_plan_model.py
src/agentscope/plan/_plan_notebook.py
src/agentscope/rag/_reader/_word_reader.py
tests/plan_test.py
```

**Structure Decision**: Single project structure under `src/` + `tests/`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
