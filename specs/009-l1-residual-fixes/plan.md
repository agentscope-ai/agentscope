# Implementation Plan: Absorb main L1 residual fixes (models/tool-choice)

**Branch**: `009-l1-residual-fixes` | **Date**: 2026-02-12 | **Spec**: specs/009-l1-residual-fixes/spec.md
**Input**: Feature specification from `/specs/009-l1-residual-fixes/spec.md`

## Summary

Absorb a scoped residual L1 bundle from local `main` to `easy` with minimal
risk:

- Apply `28547e7` (deprecated `tool_choice="any"` warning control and mapping).
- Apply `303f0f9` (Qwen-Omni audio input format normalization).
- Validate `3b67178` is already equivalent on `easy` (no-op, no extra diff).

Defer large formatter refactors (`bd5d926`, `f5fdc37`) to a separate feature due
blast radius and conflict risk.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Primary Dependencies**: Managed in `pyproject.toml`
**Storage**: N/A
**Testing**: pytest + pre-commit + ruff + pylint
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Performance Goals**: N/A
**Constraints**: Keep easy-only ecosystems unchanged; minimal diff
**Scale/Scope**: Model/tool-choice module-level changes in `src/agentscope/model/*` and `src/agentscope/__init__.py`

## Constitution Check

*GATE: Must pass before implementation.*

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface/schema change requiring SOP edits in this batch.
- [x] **Zero-deviation contract**: no public schema surface is expanded.
- [x] **Security boundaries**: no secret handling or I/O boundary changes.
- [x] **Quality gates**: pre-commit + ruff + pylint + pytest must pass.

## Upstream Commit Targets (local main)

- `28547e7` fix(tool_choice): print warning only once for deprecated argument.
- `303f0f9` fix(model): Qwen-Omni audio input format compatibility.
- `3b67178` fix(chatmodel): `choice.delta` null-safe parse (already equivalent).

## Deferred Targets

- `bd5d926` and `f5fdc37` are intentionally excluded from this batch.

## Project Structure

### Documentation (this feature)

```text
specs/009-l1-residual-fixes/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
├── checklists/
└── tasks.md
```

### Source Code (repository root)

```text
src/agentscope/__init__.py
src/agentscope/model/_model_base.py
src/agentscope/model/_openai_model.py
src/agentscope/model/_anthropic_model.py
src/agentscope/model/_dashscope_model.py
src/agentscope/model/_gemini_model.py
src/agentscope/model/_ollama_model.py
```

**Structure Decision**: Keep changes narrowly scoped to tool-choice and
openai-model request formatting paths.

## Complexity Tracking

Not applicable. No constitution violations.
