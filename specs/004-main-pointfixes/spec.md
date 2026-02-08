# Feature Specification: Absorb main low-risk pointfixes (L1)

**Feature Branch**: `004-main-pointfixes`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-08
**Status**: Draft
**Input**: User description: "Absorb main -> easy low-risk point fixes (model/formatter/embedding/tool/utils) while preserving easy-only features."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Runtime robustness improves without contract changes (Priority: P1)

As a maintainer, I want to absorb upstream low-risk fixes so runtime crashes and
platform incompatibilities are reduced, while keeping easy-only features and
public contracts unchanged.

**Why this priority**: These are stability fixes that reduce user-facing
failures with minimal surface area change.

**Independent Test**: Existing CI test suite passes; key unit tests covering
RAG, model parsing, and tooling remain green.

**Acceptance Scenarios**:

1. **Given** a clean environment installing `.[dev]`, **When** the test suite
   runs, **Then** it passes with no new failures.
2. **Given** streaming model/tool parsing paths, **When** partial/edge response
   fragments occur, **Then** the system does not crash due to unexpected None
   values or invalid JSON shapes.
3. **Given** Windows execution of the python tool, **When** the tool runs,
   **Then** it uses UTF-8 environment defaults to avoid encoding issues.

### Edge Cases

- `pypdf` / model SDKs changing response shape (None fields, partial JSON)
- Partial tool-call JSON arguments during streaming
- Platform-specific encoding behavior on Windows

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST keep public interfaces/tool schemas unchanged.
- **FR-002**: System MUST not introduce new extras groups or packaging mode
  changes.
- **FR-003**: System MUST apply the upstream pointfixes only to the intended
  files (model/formatter/embedding/tool/utils) with minimal diff.
- **FR-004**: `_json_loads_with_repair` MUST return a dict suitable for tool
  argument parsing, even for partial JSON strings.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: CI passes with 0 test failures after absorption.
- **SC-002**: No new lint failures in the configured gates (pre-commit, ruff,
  pylint).
- **SC-003**: The absorbed fixes are limited to the targeted files (no unrelated
  refactors).
