```
Module: `src/agentscope/token`
Responsibility: Token counting and management for different model providers.
Key Types: `TokenCounter`, `TokenOutput`

Key Functions/Methods
- `count(messages)` — estimates token usage before model invocation
  - Purpose: Prevents token limit exceedence and optimizes cost management

Related SOP: `docs/token_management.md`