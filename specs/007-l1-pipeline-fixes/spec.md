# Feature Specification: Absorb main L1 pipeline fixes

**Feature Branch**: `007-l1-pipeline-fixes`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-10
**Status**: Draft
**Input**: User description: "非常好，继续按 spec→plan→tasks 吸收上游：优先 L1 点修，避免 L3 大重构与 easy-only 特性冲突"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pipeline errors are not silently swallowed (Priority: P1)

As a developer, I want pipeline streaming helpers to surface errors reliably,
so that failures in agent execution do not appear as successful runs with only
partial output.

**Why this priority**: Silent failures make debugging and correctness checks
unreliable, especially when UI/log consumers depend on streamed messages.

**Independent Test**: A pipeline test that prints a message and then raises an
exception still raises that exception after streaming ends.

**Acceptance Scenarios**:

1. **Given** an agent prints at least one message and then raises an error,
   **When** the streaming pipeline is consumed to completion,
   **Then** the consumer receives the printed message(s) and the original error
   is raised.

---

### User Story 2 - RAG sentence splitting is less noisy (Priority: P2)

As a maintainer, I want RAG sentence splitting to avoid excessive download logs
in common environments, so CI logs and local runs stay readable.

**Why this priority**: Log noise obscures actionable errors and slows down
triage.

**Independent Test**: Existing RAG reader tests still pass without changing
observed outputs.

**Acceptance Scenarios**:

1. **Given** sentence splitting is enabled,
   **When** tokenizers are downloaded,
   **Then** downloads run in a quiet mode that does not spam logs.

---

### User Story 3 - DashScope multimodal embedding uses configured credentials (Priority: P2)

As a developer, I want the DashScope multimodal embedding implementation to
use the configured API key consistently, so calls do not fail unexpectedly.

**Why this priority**: Credential handling bugs are high-impact and often
misdiagnosed as service outages.

**Independent Test**: The embedding call path includes the configured
credential value when invoking the provider.

**Acceptance Scenarios**:

1. **Given** a DashScope multimodal embedding model is configured with an API
   key,
   **When** an embedding call is made,
   **Then** the provider call uses that key.

---

### User Story 4 - MsgHub accepts participant sequences (Priority: P3)

As a developer, I want MsgHub to accept any sequence of agents (not only lists),
so I can pass tuples or other sequence types without type friction.

**Why this priority**: This is a low-risk ergonomics improvement that reduces
type-related churn and supports broader call sites.

**Independent Test**: Construct MsgHub with a tuple of agents and confirm it
behaves equivalently to a list of agents.

**Acceptance Scenarios**:

1. **Given** a tuple of agents,
   **When** MsgHub is constructed with that tuple,
   **Then** it initializes successfully and uses those agents as participants.

### Edge Cases

- Pipeline tasks that fail after emitting some streamed output.
- Sentence splitting environments where NLTK resources are missing.
- Provider SDK behavior changes (e.g., requiring an explicit API key field).
- MsgHub construction with non-list sequences (e.g., tuples).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve easy-only features and not introduce any
  L3 refactors as part of this absorption.
- **FR-002**: The system MUST NOT change public tool schemas or add new
  contract surface.
- **FR-003**: The pipeline streaming helper MUST raise the underlying task
  exception after streaming ends.
- **FR-004**: Sentence splitting downloads MUST run in quiet mode.
- **FR-005**: DashScope multimodal embedding provider calls MUST include the
  configured API key.
- **FR-006**: MsgHub MUST accept a sequence of agents as participants.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q tests`
  passes.
- **SC-004**: The new pipeline error-propagation scenario is covered by a test.
- **SC-005**: A MsgHub construction scenario using a tuple of agents is covered
  by a test.
