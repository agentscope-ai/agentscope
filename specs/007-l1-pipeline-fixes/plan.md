# Implementation Plan: Absorb main L1 pipeline fixes

**Branch**: `007-l1-pipeline-fixes` | **Date**: 2026-02-10 | **Spec**: specs/007-l1-pipeline-fixes/spec.md
**Input**: Feature specification from `/specs/007-l1-pipeline-fixes/spec.md`

## Summary

Absorb a small, low-risk set of upstream point fixes from local `main` into
`easy`:

- Pipeline: ensure `stream_printing_messages` surfaces task exceptions.
- Pipeline: allow MsgHub to accept a generic participant sequence.
- RAG: reduce NLTK download log noise.
- Embedding: ensure DashScope multimodal calls include configured API key.

Constraints: preserve easy-only features; no public contract or tool schema
changes; minimal diffs.

## Technical Context

**Language/Version**: Python 3.10+ (library; `setup.py` declares `>=3.10`)
**Primary Dependencies**: Managed in `setup.py` (install_requires + extras)
**Storage**: N/A
**Testing**: pytest + pre-commit + ruff + pylint
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Performance Goals**: N/A
**Constraints**: No L3 refactors; keep easy-only ecosystems unchanged
**Scale/Scope**: Small surface area change across 4 files + tests

## Constitution Check

*GATE: Must pass before implementation.*

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface or schema changes; SOP updates not required.
- [x] **Zero-deviation contract**: no tool/schema surface change.
- [x] **Security boundaries**: no secret handling or I/O scope changes.
- [x] **Quality gates**: run pre-commit, ruff, pylint, pytest and keep green.

## Upstream Commit Targets (local main)

- `51145a9` MsgHub typing: accept `Sequence[AgentBase]`, internalize as list.
- `1f1946d` TextReader: use quiet NLTK downloads.
- `da5a6e9` DashScope multimodal embedding: include `api_key` in provider call.
- `ca5718d` stream_printing_messages: raise task exception after draining.

## Project Structure

### Documentation (this feature)

```text
specs/007-l1-pipeline-fixes/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```text
src/agentscope/pipeline/_functional.py
src/agentscope/pipeline/_msghub.py
src/agentscope/rag/_reader/_text_reader.py
src/agentscope/embedding/_dashscope_multimodal_embedding.py
tests/pipeline_test.py
```

**Structure Decision**: Single package under `src/agentscope/` with tests in
`tests/`.

## Complexity Tracking

Not applicable. No constitution violations.
