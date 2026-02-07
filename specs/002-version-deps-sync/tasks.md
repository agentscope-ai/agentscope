---

description: "Tasks for version and dependency alignment (A+B)"

---

# Tasks: Version and Dependency Alignment (A+B)

**Input**: Design documents from `/specs/002-version-deps-sync/`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: No new tests requested by the spec; rely on existing CI quality gates.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Constitution Gates (applies to all tasks)

- If the change touches public interfaces / tool schemas: update the relevant `docs/**/SOP.md`, add `todo.md` acceptance items,
  and include field-set-equality contract tests.
- Run quality gates in `./.venv` (e.g. `ruff check src` + relevant `pytest`) before marking tasks complete.

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Confirm target version and dependency decisions from specs/002-version-deps-sync/research.md

---

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T002 Identify version update target in src/agentscope/_version.py (current vs target)
- [ ] T003 Identify dependency pin translation needed for setup.py (mem0ai, OpenTelemetry)

---

## Phase 3: User Story 1 - Release Metadata Matches Target (Priority: P1)

**Goal**: Package version metadata matches the target release.

**Independent Test**: Query package version and confirm it matches the target release version.

### Implementation for User Story 1

- [ ] T004 [US1] Update version string in src/agentscope/_version.py to the target release
- [ ] T005 [US1] Verify version query behavior via standard package metadata inspection

**Checkpoint**: Version metadata matches the target release.

---

## Phase 4: User Story 2 - Dependency Constraints Avoid Known-Bad Releases (Priority: P1)

**Goal**: Dependency constraints exclude known-bad upstream versions and enforce stable baselines.

**Independent Test**: Inspect dependency constraints in setup.py and confirm the intended bounds.

### Implementation for User Story 2

- [ ] T006 [US2] Apply mem0ai upper-bound constraint in setup.py extras (align with upstream <=0.1.116)
- [ ] T007 [US2] Apply OpenTelemetry minimum versions in setup.py install_requires (>=1.39.0) and add semantic conventions baseline
- [ ] T008 [US2] Confirm no new extras groups were introduced in setup.py

**Checkpoint**: Dependency constraints match the research decisions and preserve extras structure.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [ ] T009 Run quality gates in ./.venv (ruff check src + relevant pytest)
- [ ] T010 Validate quickstart checklist in specs/002-version-deps-sync/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - blocks user story tasks
- **User Stories (Phases 3-4)**: Depend on Foundational phase completion
- **Polish (Phase 5)**: Depends on user story completion

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - no dependency on US2
- **User Story 2 (P1)**: Can start after Foundational - no dependency on US1

### Parallel Opportunities

- T004 and T006/T007 can run in parallel after Phase 2 because they touch different files
- T005 and T008 can run in parallel once their preceding updates are complete
