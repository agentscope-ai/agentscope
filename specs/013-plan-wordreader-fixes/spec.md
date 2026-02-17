# Feature Specification: Absorb plan notebook and word reader bugfixes (58a4858)

**Feature Branch**: `013-plan-wordreader-fixes`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-16
**Status**: Draft
**Input**: User description: "深入 58a4858（plan + word reader 修复）并按 spec→plan→tasks 吸收上游，避免与 easy-only 特性冲突"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan state is persisted and recoverable (Priority: P1)

As a maintainer, I want plan state to serialize and deserialize complete fields
reliably, so agent runs can resume correctly without losing plan metadata.

**Why this priority**: Plan state loss or inconsistent state transitions break core
agent continuity and are hard to debug once persisted.

**Independent Test**: A plan with subtask state transitions can be saved,
finished, loaded into a new notebook, and preserved exactly.

**Acceptance Scenarios**:

1. **Given** a current plan with subtasks, **When** state is exported,
   **Then** plan identifiers and outcome-related fields are included.
2. **Given** a finished historical plan in storage, **When** state is loaded,
   **Then** plan and subtask fields match the exported state exactly.

---

### User Story 2 - Subtask state updates keep plan state coherent (Priority: P1)

As a maintainer, I want subtask updates to refresh parent plan state, so the
notebook reflects whether work is in progress without manual correction.

**Why this priority**: Mismatched subtask/plan states create behavior ambiguity
for tools and downstream automation.

**Independent Test**: Changing a subtask to `in_progress` updates plan state,
and completing subtasks keeps expected plan-level state until finish action.

**Acceptance Scenarios**:

1. **Given** a todo plan, **When** a subtask is marked `in_progress`,
   **Then** plan state becomes `in_progress`.
2. **Given** subtask update APIs, **When** invalid legacy state names are used,
   **Then** only supported states are accepted.

---

### User Story 3 - Word reader runtime path is robust and import-safe (Priority: P2)

As a maintainer, I want Word reader typing/runtime imports to be separated
cleanly, so docs with tables/images can be parsed while avoiding import-time
side effects.

**Why this priority**: This lowers compatibility risk across environments while
preserving existing reader behavior.

**Independent Test**: Word reader can process supported structures with runtime
imports available, and does not regress type-related execution.

**Acceptance Scenarios**:

1. **Given** a Word document with paragraph/table/image elements,
   **When** the reader runs,
   **Then** extraction logic executes with runtime imports resolved correctly.
2. **Given** type checking contexts,
   **When** static analysis runs,
   **Then** type hints remain valid without importing runtime-only objects.

### Edge Cases

- Historical plan storage contains multiple finished plans and must preserve
  ordering and payload shape after serialization.
- A plan already in `done`/`abandoned` state must not be implicitly reset by
  subtask refresh logic.
- Word reader must keep behavior consistent for URL/base64 image handling in
  mixed content documents.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only upstream commit `58a4858` scope in
  this batch.
- **FR-002**: In-memory plan storage MUST serialize and deserialize historical
  plans with full `Plan` payload fidelity.
- **FR-003**: `Plan` and `SubTask` persisted state MUST include id/outcome/time
  fields required for accurate recovery.
- **FR-004**: Updating subtask state MUST refresh plan state between `todo` and
  `in_progress` where applicable, while preserving terminal states.
- **FR-005**: Plan notebook APIs MUST use `abandoned` terminology consistently
  (not legacy `deprecated`).
- **FR-006**: Word reader imports MUST remain runtime-safe and type-checking
  friendly without changing easy-only public contracts.
- **FR-007**: Regression tests MUST cover serialization and transition behavior
  for plan notebook changes.

### Key Entities *(include if feature involves data)*

- **Plan**: Parent task object with id, lifecycle state, outcome, timestamps,
  and subtask list.
- **SubTask**: Atomic step under a plan with independent state/outcome/timing.
- **InMemoryPlanStorage**: Ordered historical plan store persisted through
  state module serialization.
- **Word Reader Blocks**: Paragraph/table/image extracted structures used for
  downstream RAG chunking.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes on the feature branch.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: `tests/plan_test.py` passes in CI for this branch.
- **SC-004**: The feature diff is limited to targeted plan/word-reader files
  and `specs/013-plan-wordreader-fixes/*`.
