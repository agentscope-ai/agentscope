# Feature Specification: Absorb main ReActAgent L1 fixes

**Feature Branch**: `008-reactagent-l1-fixes`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-10
**Status**: Draft
**Input**: User description: "非常好，继续按 spec→plan→tasks 吸收上游：优先 L1 点修，避免 L3 大重构与 easy-only 特性冲突"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - ReActAgent does not silently lose memory writes (Priority: P1)

As a maintainer, I want ReActAgent to persist long-term memory writes in all
relevant reply paths, so stateful behavior remains consistent and observable.

**Why this priority**: Missing memory writes create subtle behavioral drift
across turns and reduce trust in memory-backed workflows.

**Independent Test**: Existing agent tests and a focused ReAct flow confirm that
memory record hooks are still called before returning final replies.

**Acceptance Scenarios**:

1. **Given** a ReActAgent reply flow that returns a final message,
   **When** the response path completes,
   **Then** `record_to_memory` logic is invoked before the reply is returned.

---

### User Story 2 - ReActAgent interruption handling is parameter-safe (Priority: P1)

As a maintainer, I want interruption handling to pass correct parameters,
so cancellation behavior is deterministic and does not fail due to signature
mismatch.

**Why this priority**: Interruption paths are safety-critical; parameter
mismatch can break cancellation recovery.

**Independent Test**: Interrupt path tests complete without argument mismatch
errors and preserve expected cancellation behavior.

**Acceptance Scenarios**:

1. **Given** an interrupted ReActAgent execution,
   **When** interrupt handlers are invoked,
   **Then** handler calls use the corrected parameter set.

---

### User Story 3 - Retrieval text join ignores None fragments (Priority: P2)

As a maintainer, I want retrieval text assembly to ignore None fragments,
so prompt construction does not degrade due to nullable pieces.

**Why this priority**: Null-safe prompt assembly prevents avoidable runtime
errors and malformed retrieval context.

**Independent Test**: Retrieval path tests pass when intermediate message text
contains None values.

**Acceptance Scenarios**:

1. **Given** retrieval content with mixed text and None fragments,
   **When** the retrieval text is joined,
   **Then** None fragments are filtered out before concatenation.

### Edge Cases

- Multiple return paths in ReActAgent reply pipeline must still trigger memory
  recording.
- Interrupt handling during tool execution vs. reasoning execution.
- Retrieval context containing sparse or partially-null content blocks.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only the targeted ReActAgent L1 fixes from
  upstream in this feature.
- **FR-002**: The system MUST preserve easy-only features and avoid unrelated
  refactors.
- **FR-003**: ReActAgent reply logic MUST call memory recording before final
  return in the corrected paths.
- **FR-004**: ReActAgent interruption logic MUST use the corrected handler
  parameter passing.
- **FR-005**: Retrieval text joining MUST filter out None values before join.

### Key Entities *(include if feature involves data)*

- **ReActAgent Reply Flow**: The sequence of reasoning/acting/summarizing steps
  that produces the final reply and metadata.
- **Interruption Handling Path**: The logic that handles cancellation and
  delegates to interrupt handlers.
- **Retrieval Text Assembly**: The process of combining retrieved text fragments
  into prompt-ready content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
  passes.
- **SC-004**: The feature diff remains scoped to ReActAgent logic plus
  necessary docs/spec artifacts.
