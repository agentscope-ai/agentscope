# Implementation Plan: Align packaging to pyproject.toml (keep full/dev extras)

**Branch**: `006-packaging-pyproject` | **Date**: 2026-02-08 | **Spec**: specs/006-packaging-pyproject/spec.md
**Input**: Feature specification from `specs/006-packaging-pyproject/spec.md`

## Summary

Migrate easy branch packaging from `setup.py` to `pyproject.toml` (PEP 621),
matching upstream `main` habits while preserving easy-only behavior and the
existing `full`/`dev` extras strategy.

This is a packaging-only change: no runtime behavior changes, no API/schema
changes.

## Technical Context

**Language/Version**: Python 3.10+ (`requires-python >= 3.10`)
**Packaging**: setuptools backend via `pyproject.toml`
**Tests/Gates**: pre-commit + ruff + pylint + pytest (CI)
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`)

## Structure Decision

- Adopt `pyproject.toml` structure aligned with upstream `main`:
  - `[project]` with `dependencies`
  - `[project.optional-dependencies]` with `full` and `dev`
  - `dynamic = ["version"]` from `agentscope._version.__version__`
  - setuptools `packages = find(where=["src"])`
  - include `py.typed` in package data
- Remove `setup.py` to avoid having two sources of packaging truth.

## Dependency Strategy (Phase 1)

- Keep `dependencies` aligned with current easy `setup.py` minimal requirements
  (do not slim aggressively in this feature).
- Keep extras keys exactly `full` and `dev`.
- Keep `dev` as `full` + dev tools (explicitly listed; no self-referential
  `agentscope[full]` dependency to avoid recursion).
- Preserve easy-only optional-dep constraints:
  - `pymilvus[milvus_lite]` must remain excluded on Windows via marker.
- Where upstream `main` has stability pins that reduce conflicts (e.g.,
  `qdrant-client`), adopt them if they don't break easy behavior.

## Implementation Steps

1. Add `pyproject.toml` with PEP 621 metadata and dependency lists.
2. Remove `setup.py` and ensure no CI/workflow depends on it.
3. Verify:
   - `python -m build` produces artifacts
   - wheel install/import works
   - `pip install -e '.[dev]'` works in CI
4. Run repo gates (pre-commit / ruff / pylint / pytest subset locally).

## Verification

- `./.venv/bin/pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`
- `./.venv/bin/python -m build`
- `./.venv/bin/pip install dist/*.whl` + `./.venv/bin/python -c "import agentscope; print(agentscope.__version__)"`

