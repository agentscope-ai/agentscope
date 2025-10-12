```
Module: `src/agentscope/exception`
Responsibility: Custom exception hierarchy for error handling across agent operations.
Key Types: `AgentBaseException`, `ModelInvocationException`, `ToolExecutionException`

Key Functions/Methods
- `__init__(message, *args)` — specialized constructor for different error types
  - Purpose: Provides structured error information for debugging and recovery
  - Inputs: Error message, optional context data
  - Returns: Exception instances with context
  - Side‑effects: Error state information for agent troubleshooting
  - References: `src/agentscope/exception/__init__.py`