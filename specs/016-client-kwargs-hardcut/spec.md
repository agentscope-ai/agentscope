# Feature Specification: Hard-cut chat model client kwargs naming

**Feature Branch**: `016-client-kwargs-hardcut`  
**Base Branch**: `easy`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User-approved plan to absorb upstream model kwargs unification with hard cut

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unified constructor kwarg naming (Priority: P1)

As a maintainer, I want OpenAI/Gemini/Anthropic chat models to expose only
`client_kwargs` so provider constructors use one consistent naming contract.

**Why this priority**: Inconsistent parameter names increase integration errors
and fragment examples/tests.

**Independent Test**: Each model can be instantiated with `client_kwargs` and
the kwargs are passed to provider SDK clients.

**Acceptance Scenarios**:

1. **Given** `OpenAIChatModel(..., client_kwargs={"timeout": 30})`, **When** it
   is initialized, **Then** client init receives `timeout=30`.
2. **Given** `GeminiChatModel(..., client_kwargs={"timeout": 30})`, **When** it
   is initialized, **Then** client init receives `timeout=30`.
3. **Given** `AnthropicChatModel(..., client_kwargs={"timeout": 30})`, **When**
   it is initialized, **Then** client init receives `timeout=30`.

---

### User Story 2 - Documentation and examples stay executable (Priority: P1)

As a developer, I want docs/examples to use `client_kwargs` so copied code does
not fail with unexpected keyword arguments.

**Why this priority**: Examples are operational guidance and must match runtime
contract.

**Independent Test**: Target tutorial/example files contain only
`client_kwargs` for chat model constructor injection.

**Acceptance Scenarios**:

1. **Given** tutorial snippets for OpenAI-compatible endpoints,
   **When** I follow them, **Then** they pass `client_kwargs` instead of
   deprecated naming.
2. **Given** agent examples using OpenAIChatModel, **When** they build model
   clients, **Then** they pass `client_kwargs`.

---

### User Story 3 - SOP contract is aligned (Priority: P2)

As a maintainer, I want `docs/model/SOP.md` to reflect `client_kwargs` so
module contract and implementation remain consistent.

**Why this priority**: SOP is the project’s authority for module behavior.

**Independent Test**: SOP no longer describes `client_args` as the chat-model
client injection parameter.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: OpenAI/Gemini/Anthropic chat model constructors MUST expose
  `client_kwargs` for SDK client initialization.
- **FR-002**: This batch MUST hard-cut `client_args` in these constructors
  (no compatibility alias in this batch).
- **FR-003**: Existing model unit tests MUST validate `client_kwargs`
  propagation to provider SDK clients.
- **FR-004**: Affected tutorials and examples MUST use `client_kwargs`.
- **FR-005**: `docs/model/SOP.md` MUST document `client_kwargs` consistently.

### Edge Cases

- Passing `client_kwargs=None` must keep previous default behavior.
- Generated request kwargs behavior is unchanged; only constructor client kwarg
  naming changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No `client_args` occurrences remain under `src/`, `tests/`,
  `docs/`, and `examples/`.
- **SC-002**: Focused model tests for OpenAI/Gemini/Anthropic pass locally.
- **SC-003**: Lint gates (`ruff`, `pylint -E`) pass for touched code paths.
- **SC-004**: Diff scope remains confined to model constructors,
  docs/examples/tests, and `specs/016-client-kwargs-hardcut/*`.

## Assumptions

- Breaking caller compatibility for `client_args` is accepted for this batch.
- Upstream remote sync may lag in this local environment; implementation is
  based on current local `easy` baseline.
