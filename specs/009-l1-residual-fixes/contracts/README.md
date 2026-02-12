# Contracts: 009-l1-residual-fixes

No new external API contracts are introduced in this feature.

Behavioral contract updates:

- Deprecated `tool_choice="any"` remains accepted and maps to required mode.
- Omni model audio blocks are normalized before dispatch.
