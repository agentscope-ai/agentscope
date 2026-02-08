---

description: "Retire CLAUDE docs; SOP-only normative source"

---

# Tasks: Retire CLAUDE docs; SOP is the only normative source

**Input**: `specs/005-retire-claude/spec.md`, `specs/005-retire-claude/plan.md`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Branch**: `005-retire-claude`

## Constitution Gates (applies to all tasks)

- If a task touches public interfaces / tool schemas: stop and follow SOP-first
  + field-set-equality contract tests.
- Run quality gates (`pre-commit`, `ruff`, `pylint`, `pytest`) before marking
  tasks complete.

## Phase 1: Inventory (P1)

- [X] T001 List all `CLAUDE.md` files (root + `src/agentscope/**`) and all
  references in `docs/**`, `AGENTS.md`, `.specify/**`.
- [X] T002 Identify modules whose SOP relies on `src/**/CLAUDE.md` for
  entrypoints/call-chains; record the minimal content that must be migrated.

---

## Phase 2: Stop Regeneration (P1)

- [X] T003 [US1] Ensure no repo-tracked workflow creates/updates `CLAUDE.md`
  (note: `.specify/` is tool-managed and not versioned in this repo); enforce
  via guard hook (see T009).

---

## Phase 3: SOP Self-Containment (P1)

- [X] T004 [US2] Update `docs/SOP.md` to remove CLAUDE references and keep SOP
  as the only normative source.
- [X] T005 [US2] Update all `docs/*/SOP.md` to remove references to
  `src/**/CLAUDE.md`. Where needed, add minimal entrypoint/call-chain guidance
  into sections 2/4.
- [X] T006 [US1] Update `AGENTS.md` to remove CLAUDE references (keep jump links
  to SOPs).

---

## Phase 4: Delete CLAUDE Artifacts (P1)

- [X] T007 [US1] Delete root `CLAUDE.md`.
- [X] T008 [US1] Delete all `src/agentscope/**/CLAUDE.md` files.

---

## Phase 5: Guards + Verification (P1)

- [X] T009 Add a lightweight guard to prevent reintroduction (e.g., a simple
  pre-commit local hook or CI grep step).
- [X] T010 Run `pre-commit run --all-files`.
- [X] T011 Run `./.venv/bin/python -m ruff check src`.
- [X] T012 Run `./.venv/bin/python -m pylint -E src`.
- [X] T013 Run a focused pytest subset if any Python logic changed (otherwise
  skip). (Skipped: no runtime Python logic changes in this feature.)
- [X] T014 Verify:
  - `find . -name CLAUDE.md` returns empty
  - `rg -n "CLAUDE\\.md" docs AGENTS.md .specify src` returns empty
- [X] T015 Commit with message like: `docs: retire CLAUDE program memory`.
