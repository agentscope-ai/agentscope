# Feature Specification: Absorb model client_kwargs unification (DashScope/Ollama)

**Feature Branch**: `012-client-kwargs-unify`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-13
**Status**: Draft
**Input**: User description: "吸收 main->easy：统一 DashScope/Ollama 模型的 client_kwargs 注入方式（141b2c4），保持其他模型的 client_args 契约不变"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DashScope model constructor is tolerant to extra kwargs (Priority: P1)

As a maintainer, I want `DashScopeChatModel` to accept extra keyword arguments
without failing, so shared initialization paths that pass `client_kwargs` do
not crash.

**Why this priority**: Constructor TypeErrors are high-impact and easy to
trigger when callers try to unify model initialization.

**Independent Test**: `DashScopeChatModel.__init__` can be bound with an
unexpected kwarg without raising.

**Acceptance Scenarios**:

1. **Given** `DashScopeChatModel` is constructed with an extra kwarg,
   **When** it is instantiated,
   **Then** it does not raise a TypeError.

---

### User Story 2 - Ollama supports client_kwargs and generate_kwargs (Priority: P1)

As a maintainer, I want `OllamaChatModel` to accept `client_kwargs` for client
initialization and `generate_kwargs` for default generation options, so callers
can pass configuration in a consistent way.

**Why this priority**: This reduces ad-hoc parameter plumbing and improves
compatibility across providers.

**Independent Test**: Signatures include these parameters and default
generation kwargs are merged into call-time kwargs.

**Acceptance Scenarios**:

1. **Given** `OllamaChatModel(client_kwargs=..., generate_kwargs=...)`,
   **When** the model is called,
   **Then** request kwargs include the default generation kwargs unless
   overridden.

---

### User Story 3 - Preserve existing client_args contract for other models (Priority: P2)

As a maintainer, I want OpenAI/Gemini/Anthropic to keep `client_args` as the
primary injection parameter in easy, so docs/tutorial/tests remain aligned.

**Why this priority**: `docs/model/SOP.md` and tutorials currently define
`client_args` as the contract; changing it would be a larger batch.

**Independent Test**: Existing tests referencing `client_args` still type-check
and lint gates pass.

**Acceptance Scenarios**:

1. **Given** existing code uses `client_args` for OpenAI/Gemini/Anthropic,
   **When** this feature is merged,
   **Then** there is no breaking change to those call sites.

### Edge Cases

- Call-time kwargs override default generate kwargs for Ollama.
- Extra kwargs are accepted by DashScope but do not change its behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only the low-risk client_kwargs unification
  scope for DashScope/Ollama from upstream in this feature.
- **FR-002**: The system MUST preserve easy-only behavior and avoid unrelated
  refactors.
- **FR-003**: `DashScopeChatModel` MUST accept extra kwargs without raising.
- **FR-004**: `OllamaChatModel` MUST support `client_kwargs` and
  `generate_kwargs` without breaking existing initialization.
- **FR-005**: OpenAI/Gemini/Anthropic MUST keep `client_args` as their
  constructor injection parameter in easy for this batch.

### Key Entities *(include if feature involves data)*

- **Client Kwargs**: Keyword arguments used to initialize an SDK client.
- **Generate Kwargs**: Default keyword arguments used for API generation calls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: Focused runtime checks for signatures and kwargs merge pass.
- **SC-004**: Feature diff remains scoped to model files and
  `specs/012-client-kwargs-unify/*`.
