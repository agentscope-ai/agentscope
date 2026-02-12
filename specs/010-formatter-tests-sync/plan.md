# Implementation Plan: Absorb main formatter unit test ID consistency fix

**Branch**: `010-formatter-tests-sync` | **Date**: 2026-02-12 | **Spec**: specs/010-formatter-tests-sync/spec.md
**Input**: Feature specification from `/specs/010-formatter-tests-sync/spec.md`

## Summary

Absorb a single upstream L1 tests-only fix from local `main` into `easy`:

- `19cba5c` aligns formatter unit test fixtures and ground-truth references so
  tool use IDs and tool result IDs are consistent.

No runtime code changes. No SOP contract updates.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Primary Dependencies**: Managed in `pyproject.toml`
**Storage**: N/A
**Testing**: pytest + pre-commit + ruff + pylint
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Constraints**: tests-only; minimal diff; keep easy-only ecosystems unchanged
**Scale/Scope**: four formatter unit test files

## Constitution Check

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface/schema change; SOP update not required.
- [x] **Zero-deviation contract**: no tool/schema surface change.
- [x] **Security boundaries**: no secret handling or boundary changes.
- [x] **Quality gates**: pre-commit + ruff + pylint + pytest (CI) must pass.

## Upstream Commit Target (local main)

- `19cba5c` ci(formatter): improve formatter unit tests with consistent tool
  use and tool result ID

## Project Structure

### Documentation (this feature)

```text
specs/010-formatter-tests-sync/
├── spec.md
├── plan.md
├── tasks.md
├── quickstart.md
├── research.md
├── data-model.md
├── contracts/
└── checklists/
```

### Source Code (repository root)

```text
tests/formatter_anthropic_test.py
tests/formatter_deepseek_test.py
tests/formatter_gemini_test.py
tests/formatter_ollama_test.py
```
