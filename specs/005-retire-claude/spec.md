# Feature Specification: Retire CLAUDE docs; SOP is the only normative source

**Feature Branch**: `005-retire-claude`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-08
**Status**: Draft
**Input**: User description: "摒弃 CLAUDE 这类残留设计；只保留 SOP 作为功能定义，specs 作为功能开发河流。"

## Clarifications

- This change is documentation + workflow only. No runtime behavior, APIs, or
  tool schemas should change.
- `docs/**/SOP.md` remains the only normative specification. Other files may
  link to SOP, but must not restate rules in a conflicting way.
- `specs/###-*/` remains the change stream for feature development (drafts,
  iteration, trade-offs, tasks, and acceptance).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No CLAUDE artifacts exist or regenerate (Priority: P1)

As a maintainer, I want to fully remove the CLAUDE “program memory” document
system so there is no second source of truth and no accidental regeneration by
workflow scripts.

**Why this priority**: Conflicting “rules” across README/CLAUDE/SOP silently
erodes SOP-first governance. Removing the alternate channel prevents drift.

**Independent Test**:

- No `CLAUDE.md` file exists in the repository.
- No non-spec docs or scripts reference `CLAUDE.md`.

**Acceptance Scenarios**:

1. **Given** a clean checkout, **When** I search the repository for files named
   `CLAUDE.md`, **Then** none exist.
2. **Given** the `.specify` workflow scripts, **When** I search them for
   `CLAUDE.md`, **Then** no logic exists that updates or creates such files.

---

### User Story 2 - SOP absorbs the useful “entrypoints/call-chains” (Priority: P1)

As a maintainer, I want the useful content that previously lived in CLAUDE docs
(entrypoints, call-chains, responsibility boundaries) to live in the module SOP
sections instead, so SOP remains self-contained for implementation and review.

**Why this priority**: If removing CLAUDE makes SOP incomplete, developers will
recreate ad-hoc memory docs again.

**Independent Test**:

- Module SOPs do not reference module-level `src/**/CLAUDE.md`.
- SOP sections 2/4 contain the minimal entrypoints and cross-module call chains.

**Acceptance Scenarios**:

1. **Given** any module SOP under `docs/*/SOP.md`, **When** I read the “file
   mapping” and “interactions/call-chains” sections, **Then** I can locate the
   relevant code entrypoints without relying on any CLAUDE doc.

### Edge Cases

- References to `CLAUDE.md` in templates or scripts that silently recreate it.
- Links in SOPs that point to `src/**/CLAUDE.md` paths.
- Non-repo tooling expecting `CLAUDE.md` to exist (should fail fast with a clear
  message or be updated to use SOP).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST remove root `CLAUDE.md` and all `src/**/CLAUDE.md`
  files from the repository.
- **FR-002**: System MUST remove `CLAUDE.md` references from non-spec docs and
  workflow scripts (e.g., `docs/**`, `AGENTS.md`, `.specify/**`).
- **FR-003**: System MUST migrate the minimal “entrypoints/call-chains” content
  into module SOP sections 2/4 where needed.
- **FR-004**: System MUST NOT change runtime behavior, public APIs, or tool
  schemas as part of this cleanup.
- **FR-005**: Quality gates MUST remain green (pre-commit, ruff, pylint, pytest
  as applicable).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `find . -name CLAUDE.md` returns no files.
- **SC-002**: `rg -n "CLAUDE\\.md" docs AGENTS.md .specify src` returns no hits.
- **SC-003**: `pre-commit run --all-files` passes.
- **SC-004**: `./.venv/bin/python -m ruff check src` and `./.venv/bin/python -m pylint -E src`
  pass with 0 errors.

