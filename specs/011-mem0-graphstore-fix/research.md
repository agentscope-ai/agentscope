# Research: 011-mem0-graphstore-fix

## Decision 1: Treat `relations` as retrieval-visible content

- **Decision**: When mem0 search returns a `relations` field, include it in the
  returned retrieval text as formatted strings.
- **Rationale**: Graphstore memory is otherwise lost to the caller.
- **Alternative**: Ignore relations (rejected: breaks graphstore utility).

## Decision 2: Structured tool-call output only when tools are present

- **Decision**: In AgentScopeLLM adapter, return `{"content": ..., "tool_calls": ...}`
  only when tools are provided; otherwise keep returning strings.
- **Rationale**: Avoids behavior changes for non-tool callers while enabling
  mem0 tool-call compatibility.
