# Implementation Plan: Absorb mem0 graphstore compatibility fix (L1)

**Branch**: `011-mem0-graphstore-fix` | **Date**: 2026-02-13 | **Spec**: specs/011-mem0-graphstore-fix/spec.md
**Input**: Feature specification from `/specs/011-mem0-graphstore-fix/spec.md`

## Summary

Absorb a single upstream L1 fix bundle from local `main` into `easy`:

- `233915d` ensures Mem0LongTermMemory includes graphstore `relations` in
  retrieval outputs.
- `233915d` updates the mem0 LLM adapter so tool-call responses can be returned
  in mem0-compatible structured form when tools are present.

No dependency changes and no SOP contract updates are expected.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Primary Dependencies**: optional `mem0ai` (pinned in extras)
**Storage**: mem0 vector store + optional graph store
**Testing**: pre-commit + ruff + pylint + pytest (CI)
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Constraints**: minimal diff; preserve easy-only behavior
**Scale/Scope**: `src/agentscope/memory/_mem0_long_term_memory.py`, `src/agentscope/memory/_mem0_utils.py`, plus example

## Constitution Check

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface/schema change requiring SOP edits.
- [x] **Zero-deviation contract**: no tool/schema surface change.
- [x] **Security boundaries**: no secret handling or I/O boundary changes.
- [x] **Quality gates**: pre-commit + ruff + pylint + pytest (CI) must pass.

## Upstream Commit Target (local main)

- `233915d` fix(memory): fix the case when `Mem0LongTermMemory` uses graphstore

## Project Structure

### Documentation (this feature)

```text
specs/011-mem0-graphstore-fix/
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
src/agentscope/memory/_mem0_long_term_memory.py
src/agentscope/memory/_mem0_utils.py
examples/functionality/long_term_memory/mem0/memory_example.py
```
