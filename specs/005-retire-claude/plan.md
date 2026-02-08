# Implementation Plan: Retire CLAUDE docs; SOP is the only normative source

**Branch**: `005-retire-claude` | **Date**: 2026-02-08 | **Spec**: specs/005-retire-claude/spec.md
**Input**: Feature specification from `specs/005-retire-claude/spec.md`

## Summary

Remove the CLAUDE “program memory” document system (root + module-level
`src/**/CLAUDE.md`) to enforce a single normative source of truth:
`docs/**/SOP.md`. Keep `specs/###-*/` as the iterative change stream (draft
spec/plan/tasks). Migrate any essential entrypoint/call-chain guidance into the
relevant module SOP sections.

## Technical Context

**Language/Version**: Python 3.10+ (library; `setup.py` declares `>=3.10`)
**Primary Dependencies**: Managed in `setup.py`
**Testing**: pre-commit + ruff + pylint + pytest
**Target Platform**: GitHub Actions (Ubuntu/Windows/macOS)
**Project Type**: Single package (`src/agentscope`, `tests/`, `docs/`, `specs/`)

## Scope

In scope:

- Delete root `CLAUDE.md`.
- Delete all module-level docs named `src/agentscope/**/CLAUDE.md`.
- Update `docs/SOP.md` and `docs/*/SOP.md` to remove references to CLAUDE docs
  and to be self-contained (entrypoints + call chains in sections 2/4).
- Update `AGENTS.md` to remove CLAUDE references (keep SOP-first and jump links).
- Update `.specify/scripts/bash/update-agent-context.sh` to stop creating or
  updating `CLAUDE.md` and to avoid default creation of a CLAUDE file when no
  agent files exist.
- Add a lightweight guard to prevent reintroduction (repo grep check integrated
  into an existing gate).

Out of scope:

- Any runtime behavior change (Agent/Toolkit/Model/etc).
- Any public API change or tool schema change.
- Packaging changes (no new extras/groups).

## Constraints

- SOP remains the only normative spec source. `README*`/`AGENTS.md`/templates
  may link to SOP but must not introduce conflicting rules.
- Keep diffs minimal and mechanical where possible (delete + remove references).

## Verification

- `./.venv/bin/pre-commit run --all-files`
- `./.venv/bin/python -m ruff check src`
- `./.venv/bin/python -m pylint -E src`
- Repo checks:
  - `find . -name CLAUDE.md` returns empty
  - `rg -n "CLAUDE\\.md" docs AGENTS.md .specify src` returns empty

