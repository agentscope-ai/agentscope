# Implementation Plan: Absorb main ReActAgent L1 fixes

**Branch**: `008-reactagent-l1-fixes` | **Date**: 2026-02-10 | **Spec**: specs/008-reactagent-l1-fixes/spec.md
**Input**: Feature specification from `/specs/008-reactagent-l1-fixes/spec.md`

## Summary

Absorb a tightly scoped ReActAgent L1 fix bundle from local `main` into
`easy`:

- `dd05db2` ensure memory recording occurs before returning replies.
- `df96805` fix interrupt handler parameter passing.
- `d3c0c1d` filter None values before joining retrieval text.

No public interface/tool schema changes. No L3 refactors.

## Technical Context

**Language/Version**: Python 3.10+ (`pyproject.toml` requires-python)
**Primary Dependencies**: Managed in `pyproject.toml`
**Storage**: N/A
**Testing**: pytest + pre-commit + ruff + pylint
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`)
**Performance Goals**: N/A
**Constraints**: Keep easy-only ecosystems unchanged; minimal diff
**Scale/Scope**: Single-module code changes in `src/agentscope/agent/_react_agent.py`

## Constitution Check

*GATE: Must pass before implementation.*

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`.
- [x] **Docs-first**: no interface/schema change; SOP update not required.
- [x] **Zero-deviation contract**: no contract/tool schema surface changes.
- [x] **Security boundaries**: no secret handling or filesystem boundary change.
- [x] **Quality gates**: pre-commit + ruff + pylint + pytest must pass.

## Upstream Commit Targets (local main)

- `dd05db2` fix(ReActAgent): invoke `record_to_memory` before returning reply.
- `df96805` fix(ReActAgent): fix parameters of `handle_interrupt` call.
- `d3c0c1d` fix(ReActAgent): filter `None` values when joining retrieval text.

## Project Structure

### Documentation (this feature)

```text
specs/008-reactagent-l1-fixes/
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
src/agentscope/agent/_react_agent.py
tests/react_agent_test.py
```

**Structure Decision**: Keep changes narrowly scoped to ReActAgent and
corresponding test coverage.

## Complexity Tracking

Not applicable. No constitution violations.
