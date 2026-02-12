# Feature Specification: Absorb main formatter unit test ID consistency fix

**Feature Branch**: `010-formatter-tests-sync`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-12
**Status**: Draft
**Input**: User description: "吸收 main->easy formatter 单测一致性修复（tool_use/tool_result id 对齐），保持 tests-only"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Formatter tool IDs are consistent in unit tests (Priority: P1)

As a maintainer, I want formatter unit tests to use consistent tool use / tool
result IDs, so the test fixtures accurately reflect real tool-call correlation
and remain stable.

**Why this priority**: These tests are CI gatekeepers. Inconsistent IDs create
false negatives and reduce confidence in formatter behavior.

**Independent Test**: Running formatter unit tests confirms that tool result
references match the corresponding tool use IDs.

**Acceptance Scenarios**:

1. **Given** a unit test fixture with a second tool use block,
   **When** its tool result block is constructed,
   **Then** the tool result ID matches the tool use ID.
2. **Given** a serialized formatter output that includes tool result reference
   fields (`tool_use_id` / `tool_call_id`),
   **When** asserting ground truth,
   **Then** the referenced IDs match the tool use block ID.

### Edge Cases

- Multiple tool interactions in a single test case.
- Multi-agent formatter sequences where tool IDs are propagated.
- No runtime behavior change: this batch must remain tests-only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only the upstream formatter unit test ID
  consistency fix in this feature.
- **FR-002**: The system MUST preserve easy-only behavior by keeping this batch
  tests-only (no `src/` changes).
- **FR-003**: Unit test fixtures MUST use consistent tool IDs across
  `ToolUseBlock.id`, `ToolResultBlock.id`, and ground-truth reference fields.

### Key Entities *(include if feature involves data)*

- **Tool Use ID**: Identifier representing a tool call instance.
- **Tool Result Reference ID**: Identifier in a tool result that must match the
  tool use it corresponds to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: Formatter unit tests pass in CI for this feature.
- **SC-004**: Feature diff remains limited to `tests/formatter_*` and
  `specs/010-formatter-tests-sync/*`.
