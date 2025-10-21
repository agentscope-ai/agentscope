Module: `src/agentscope/tool/`
Responsibility: Tool registry, execution engine, and MCP integration framework
Key Functions/Methods
- `Toolkit.register_tool_function(func, group_name="basic", name=None, description=None, presets=None) → None`
  - Purpose: Register tool functions with schema inference and MCP integration
  - Inputs: Function to register, optional group and metadata
  - Returns: None
  - Side-effects: Updates internal registry; generates JSON schema for function inputs/outputs
  - References: `src/agentscope/tool/_toolkit.py:125`
  - Type Safety: Param `func` has type hints; schemas preserve function signature types

- `Toolkit.call_tool_function(tool_call) → ToolResponse`
  - Purpose: Execute tool calls with comprehensive safety and debugging support
  - Inputs: `tool_call dict` with name, arguments, call_id
  - Returns: `ToolResponse` with name, call_id, result, is_last flag
  - Side-effects: May call external APIs, execute code, or perform I/O operations
  - Errors: Raises `ToolExecutionError` for execution failures
  - Type Safety: Input validated against registered schemas; structured outputs typed

- `make_mcp_tool(server_name, tool_name, description=None) → Callable`
  - Purpose: Create callable tool functions from MCP servers
  - References: `src/agentscope/tool/_mcp_tool.py:78`

Call Graph
- `ReActAgent._acting` → `Toolkit.call_tool_function` → tool execution → `ToolResponse` → agent memory update

## Testing Strategy
- Unit tests: `tests/tool/test_toolkit.py`, `test_registered_tool_function.py`
- Integration: Cross-module tool chains testing
- Edge cases: Security boundary violations, resource cleanup, error propagation

## Related SOP: `docs/tool/SOP.md`