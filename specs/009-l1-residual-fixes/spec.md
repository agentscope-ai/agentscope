# Feature Specification: Absorb main L1 residual fixes (models/tool-choice)

**Feature Branch**: `009-l1-residual-fixes`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-12
**Status**: Draft
**Input**: User description: "继续按 spec→plan→tasks 吸收上游：优先 L1 点修，避免 L3 大重构与 easy-only 特性冲突"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deprecated tool_choice warning is controlled (Priority: P1)

As a maintainer, I want deprecated `tool_choice="any"` handling to warn in a
controlled way, so logs stay readable while preserving backward compatibility.

**Why this priority**: This path is cross-model and commonly used. Unbounded
warnings cause noisy logs and reduce operability.

**Independent Test**: Repeated calls that pass `tool_choice="any"` emit at most
one deprecation warning under the current process warning filter.

**Acceptance Scenarios**:

1. **Given** a model call with `tool_choice="any"`,
   **When** request arguments are formatted,
   **Then** behavior is translated to required tool invocation mode.
2. **Given** repeated model calls with `tool_choice="any"`,
   **When** warning is emitted,
   **Then** deprecation warning does not spam repeatedly.

---

### User Story 2 - Qwen-Omni audio input compatibility is preserved (Priority: P1)

As a maintainer, I want Qwen-Omni audio blocks to be formatted with the
required base64 prefix, so the OpenAI-compatible endpoint accepts audio input.

**Why this priority**: This is a production correctness issue for multimodal
calls and affects request success.

**Independent Test**: For model names containing `omni`, audio blocks in
formatted messages include the expected prefix before API invocation.

**Acceptance Scenarios**:

1. **Given** an audio block encoded as raw base64,
   **When** using an omni model,
   **Then** the request block is rewritten to prefixed base64 format.
2. **Given** an audio block whose data is already a URL,
   **When** using an omni model,
   **Then** URL data remains unchanged.

---

### User Story 3 - Existing delta-None fix parity is retained (Priority: P2)

As a maintainer, I want previously absorbed OpenAI streaming null-safe parsing
to remain intact, so this batch does not regress existing behavior.

**Why this priority**: `3b67178` is already equivalent in easy and should be
validated as no-op, not reintroduced with churn.

**Independent Test**: Current code path still uses null-safe access for
`choice.delta.content`.

**Acceptance Scenarios**:

1. **Given** a streaming chunk where `choice.delta` can be null-like,
   **When** text is parsed,
   **Then** parsing uses safe attribute access instead of direct dereference.

### Edge Cases

- `tool_choice="any"` must remain accepted for compatibility while warning.
- Qwen-Omni formatting should only apply to omni models.
- Existing easy-only features and ReActAgent logic are unchanged in this batch.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only low-risk residual fixes in this batch.
- **FR-002**: The system MUST preserve easy-only behavior and avoid L3 refactor
  chains.
- **FR-003**: Deprecated `tool_choice="any"` handling MUST remain functional and
  MUST emit controlled deprecation warnings.
- **FR-004**: OpenAI-compatible calls for omni models MUST normalize audio data
  format as required before request dispatch.
- **FR-005**: The existing null-safe stream delta parsing fix in easy MUST be
  preserved (no regression).
- **FR-006**: Large formatter promotion/extraction refactors (`bd5d926`,
  `f5fdc37`) MUST be deferred to a separate feature batch.

### Key Entities *(include if feature involves data)*

- **Tool Choice Mode**: A call-time control value that selects automatic/no
  tool usage or mandatory tool execution.
- **Omni Audio Input Block**: Message content block containing audio payload
  requiring provider-specific encoding.
- **OpenAI Stream Delta Chunk**: Incremental chunk element in streaming chat
  responses that may carry nullable subfields.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: Relevant focused tests for changed model behavior pass locally; CI
  remains green after merge.
- **SC-004**: Feature diff is limited to targeted model/tool-choice files and
  `specs/009-l1-residual-fixes/*`.
