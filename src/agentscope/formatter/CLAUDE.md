```
Module: `src/agentscope/formatter`
Responsibility: Multi-agent prompt formatting with tools API and truncation strategies.
Key Types: `BaseFormatter`, `PromptTemplate`, `MessageFormatter`

Key Functions/Methods
- `format(messages, tools=None, tool_constraints=None)` — converts internal messages to model-specific prompt formats
  - Purpose: Translates internal message structures to provider-specific prompt layouts
  - Inputs: Message objects, optional tool schemas
  - Returns: Formatted prompt ready for model invocation
  - Side‑effects: Potentially mutates message order/content based on provider guidelines.