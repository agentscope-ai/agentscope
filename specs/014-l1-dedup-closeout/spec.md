# Feature Specification: Close out already-absorbed L1 commits (62aa639 + 6bc219a)

**Feature Branch**: `014-l1-dedup-closeout`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-17
**Status**: Draft
**Input**: User description: "先做低风险快收口：62aa639 (py.typed) + 6bc219a (json_repair)，新开一条分支并遵循 specs 规则"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Maintainers avoid duplicate absorption work (Priority: P1)

As a maintainer, I want to verify whether selected upstream commits are already
present in easy via equivalent changes, so we avoid redundant code changes and
keep history clean.

**Why this priority**: Duplicate absorption creates unnecessary conflict risk,
noise commits, and review overhead.

**Independent Test**: Verify target commit file-level intent is already covered
by existing commits on easy and document evidence.

**Acceptance Scenarios**:

1. **Given** target commits `62aa639` and `6bc219a`, **When** commit/file history
   is inspected, **Then** equivalent code behavior exists on easy.
2. **Given** equivalent behavior is confirmed, **When** this feature closes,
   **Then** no source code files are modified.

---

### User Story 2 - Spec flow remains continuous even for no-op absorption (Priority: P2)

As a maintainer, I want a complete `spec -> plan -> tasks` trail for this
selection, so future reviewers understand why no code merge happened.

**Why this priority**: Process consistency matters for future batch planning and
traceability.

**Independent Test**: `specs/014-l1-dedup-closeout/*` contains complete
artifacts and explicit rationale.

**Acceptance Scenarios**:

1. **Given** this branch, **When** specs artifacts are reviewed,
   **Then** they clearly map target commits to equivalent easy commits.
2. **Given** the branch diff, **When** compared against easy,
   **Then** only `specs/014-l1-dedup-closeout/*` changes are present.

### Edge Cases

- Commit hash differs but behavior is already present through another absorb
  commit on easy.
- Packaging-related change exists with equivalent config but different commit
  message lineage.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The feature MUST validate equivalence for `62aa639` and `6bc219a`
  against current easy state.
- **FR-002**: The feature MUST not introduce duplicate source code edits when
  equivalence is confirmed.
- **FR-003**: The feature MUST provide evidence links (commit IDs + files + key
  lines) in `research.md`.
- **FR-004**: The feature MUST keep complete speckit artifacts under
  `specs/014-l1-dedup-closeout/`.

### Key Entities *(include if feature involves data)*

- **Target Commit**: Upstream commit requested for absorption.
- **Equivalent Commit**: Existing easy commit that already implements the
  requested behavior.
- **Evidence Record**: A structured mapping of target commit -> equivalent
  commit with file/line proof.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: Branch diff vs `easy` includes only `specs/014-l1-dedup-closeout/*`.
- **SC-003**: Both target commits have explicit equivalence proof in
  `research.md`.
