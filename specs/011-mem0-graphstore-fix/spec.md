# Feature Specification: Absorb mem0 graphstore compatibility fix (L1)

**Feature Branch**: `011-mem0-graphstore-fix`
**Base Branch**: `easy` (do not use `main/master` as mainline)
**Created**: 2026-02-13
**Status**: Draft
**Input**: User description: "吸收 main->easy：修复 Mem0LongTermMemory 在启用 graphstore 时检索 relations 丢失，以及 mem0 工具调用返回格式不兼容（233915d）"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Graphstore relations are not silently dropped (Priority: P1)

As a maintainer, I want Mem0LongTermMemory retrieval to include graphstore
relations when mem0 returns them, so graph memory is visible and usable instead
of being silently lost.

**Why this priority**: Dropping relations makes graph memory effectively
invisible and breaks expected functionality when graphstore is enabled.

**Independent Test**: A simulated mem0 search response containing `relations`
produces formatted relation strings in the returned memory text.

**Acceptance Scenarios**:

1. **Given** mem0 search results include a `relations` field,
   **When** Mem0LongTermMemory assembles retrieval output,
   **Then** relation entries are included as formatted strings.
2. **Given** mem0 search results do not include `relations`,
   **When** retrieval output is assembled,
   **Then** behavior matches current output (no regressions).

---

### User Story 2 - mem0 tool-call response format is compatible (Priority: P1)

As a maintainer, I want the AgentScopeLLM adapter to return the structured
format expected by mem0 when tools are provided, so mem0 can consume tool calls
correctly.

**Why this priority**: When mem0 uses tool calling, a plain string response can
break downstream parsing and cause memory operations to fail.

**Independent Test**: Parsing a ChatResponse containing tool_use blocks yields
`{"content": ..., "tool_calls": [...]}` when tools are present, otherwise a
string.

**Acceptance Scenarios**:

1. **Given** tools are provided to AgentScopeLLM,
   **When** the model output contains tool_use blocks,
   **Then** the adapter returns a dict with `content` and `tool_calls`.
2. **Given** tools are not provided,
   **When** the model returns text/thinking blocks,
   **Then** the adapter returns a string as before.

### Edge Cases

- Multiple relations returned in a single search response.
- Relations coexist with standard `results` memories.
- Tool-use responses that contain both thinking/text and tool_use blocks.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST absorb only `233915d` scope in this feature.
- **FR-002**: The system MUST preserve easy-only behavior and avoid unrelated
  refactors.
- **FR-003**: Retrieval output MUST include formatted relations when mem0
  returns them.
- **FR-004**: AgentScopeLLM MUST return structured dict output only when tools
  are provided; otherwise it MUST return a string.

### Key Entities *(include if feature involves data)*

- **Relation**: A graph edge in mem0 output with `source`, `relationship`, and
  `destination`.
- **Tool Call**: A structured tool invocation in model output represented by a
  `tool_use` block.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `pre-commit run --all-files` passes.
- **SC-002**: `./.venv/bin/python -m ruff check src` and
  `./.venv/bin/python -m pylint -E src` pass.
- **SC-003**: Focused runtime checks for relations formatting and tool-call
  parsing pass; CI remains green after merge.
- **SC-004**: Feature diff remains scoped to mem0 integration files plus
  `specs/011-mem0-graphstore-fix/*`.
