# Contracts: 011-mem0-graphstore-fix

No new external API contracts are introduced.

Behavioral contract notes:
- mem0 `relations` are included in retrieval text when present.
- mem0 tool-call adapter returns structured output only when tools are provided.
