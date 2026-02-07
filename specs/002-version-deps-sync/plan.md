# Implementation Plan: Version and Dependency Alignment (A+B)

**Branch**: `002-version-deps-sync` | **Date**: 2026-01-21 | **Spec**: specs/002-version-deps-sync/spec.md
**Input**: Feature specification from `/specs/002-version-deps-sync/spec.md`

**Note**: This plan is limited to version and dependency alignment (A+B) from
upstream main into easy.

## Summary

Align easy with upstream A+B changes by updating the package version to 1.0.10
and translating upstream dependency pins into easy's `setup.py` dependency
lists. No interface, schema, or tool-contract changes are introduced.

## Technical Context

**Language/Version**: Python 3.10 (per `setup.py` python_requires)  
**Primary Dependencies**: Managed in `setup.py` (install_requires + extras)  
**Storage**: N/A  
**Testing**: pytest + ruff + pylint (per CI/pre-commit)  
**Target Platform**: Cross-platform Python package  
**Project Type**: Single package under `src/agentscope/`  
**Performance Goals**: N/A  
**Constraints**: Maintain easy-only features; no schema or API changes  
**Scale/Scope**: Small surface area change (version + dependency bounds)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Branch mainline**: base branch is `easy`; PR target is `easy`; do not develop on `main/master`.
- [x] **Docs-first**: no interface or schema changes; SOP/todo updates not required for version/dependency alignment.
- [x] **Zero-deviation contract**: no tool/schema surface change in this feature.
- [x] **Security boundaries**: no changes to I/O or secrets.
- [x] **Quality gates**: run repo checks in `./.venv` (e.g. `ruff check src` + relevant `pytest`) and keep warnings at zero.

## Project Structure

### Documentation (this feature)

```text
specs/002-version-deps-sync/
├── plan.md              # This file (/speckit.plan output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
setup.py
src/agentscope/_version.py
```

**Structure Decision**: Single-package Python project under `src/agentscope/`.

## Complexity Tracking

Not applicable. No constitution violations.
