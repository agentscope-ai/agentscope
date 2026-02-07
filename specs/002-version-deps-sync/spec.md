# Feature Specification: Version and Dependency Alignment (A+B)

**Feature Branch**: `002-version-deps-sync`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-01-20
**Status**: Draft
**Input**: User description: "那么我选择A+B,请你评估后跟speckit方式进行"

## Clarifications

### Session 2026-01-21

- Q: Which target release version should easy align to for A+B? → A: 1.0.10
- Q: Should easy add opentelemetry-semantic-conventions baseline? → A: Yes, add opentelemetry-semantic-conventions>=0.60b0.
- Q: Where should the mem0ai upper-bound constraint be applied? → A: Apply mem0ai<=0.1.116 in extras_require["full"] only.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Release Metadata Matches Target (Priority: P1)

As a maintainer, I need the package to advertise the intended release version so that releases and downstream tooling can identify the correct build.

**Why this priority**: Incorrect version metadata causes downstream confusion and breaks release consistency, so this must be correct for any release process.

**Independent Test**: Verify the package reports the target version in its published metadata or version output.

**Acceptance Scenarios**:

1. **Given** a clean checkout of the easy branch, **When** the package version is queried, **Then** it matches the target release version 1.0.10.
2. **Given** a release artifact built from easy, **When** metadata is inspected, **Then** it advertises the same target version 1.0.10.

---

### User Story 2 - Dependency Constraints Avoid Known-Bad Releases (Priority: P1)

As a maintainer, I need dependency constraints adjusted to avoid known-bad upstream versions so that users get a stable installation by default.

**Why this priority**: Installation stability is critical; known-bad versions can cause runtime failures and support burden.

**Independent Test**: Inspect dependency constraints and confirm they exclude known-bad versions and reflect the intended minimums.

**Acceptance Scenarios**:

1. **Given** the dependency list for easy, **When** constraints are reviewed, **Then** the long-term memory dependency excludes the known-bad upstream version range.
2. **Given** the dependency list for easy, **When** observability dependencies are reviewed, **Then** their minimum versions match the intended stable baseline.

---

### Edge Cases

- What happens when downstream tooling expects a different versioning cadence than the target release?
- How does the system behave if a user tries to install a version outside the allowed dependency constraints?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The package MUST report the target release version 1.0.10 in its published metadata.
- **FR-002**: The package MUST report the same target release version 1.0.10 when queried programmatically.
- **FR-003**: The dependency constraints MUST exclude the known-bad upstream versions for the long-term memory dependency.
- **FR-004**: The dependency constraints MUST enforce the intended minimum versions for observability dependencies.
- **FR-005**: The dependency changes MUST preserve existing optional dependency group behavior (no new groups introduced).
- **FR-006**: The install requirements MUST include opentelemetry-semantic-conventions>=0.60b0.
- **FR-007**: The mem0ai upper-bound constraint MUST be applied only in the existing full extra.

### Key Entities *(include if feature involves data)*

- **Release Version**: The single source of truth identifier for the package build.
- **Dependency Constraints**: The allowed version ranges for upstream libraries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of version queries against the package return 1.0.10.
- **SC-002**: Dependency constraints exclude the known-bad upstream versions for the long-term memory dependency.
- **SC-003**: Dependency constraints enforce the intended minimums for observability dependencies.
- **SC-004**: Release metadata and programmatic version queries match with zero discrepancies.
- **SC-005**: Install requirements include opentelemetry-semantic-conventions>=0.60b0.

## Assumptions

- The target release version 1.0.10 is approved for the easy branch release cadence.
- The identified upstream versions are confirmed to be unstable and should be excluded.
- Adding opentelemetry-semantic-conventions>=0.60b0 is acceptable for easy users.
