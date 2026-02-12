# Data Model: 010-formatter-tests-sync

No new runtime entities are introduced.

## Concept: Tool ID Correlation (Test Fixture)

- **ToolUseBlock.id**: ID of a tool call
- **ToolResultBlock.id** (or serialized reference field): must match the tool
  call ID it corresponds to
