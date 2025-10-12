```
Module: `src/agentscope/mcp`
Responsibility: Model Context Protocol (MCP) client implementation with stateful/stateless mode support.
Key Types: `MCPClient`, `MCPTransport`, `MCPToolWrapper`

Key Functions/Methods
- `HttpStatelessClient` — HTTP‑based MCP client supporting streamable transport
  - Purpose: Provides fine‑grained control over MCP tools as local callable functions
  - Inputs: URL endpoints, API keys, transport protocols
  - Returns: Callable tool functions with MCP protocol integration

Call Graph
- `ReActAgent._acting()` → `MCPClient.get_callable_function()` → `func(address, city)`
  - References: `src/agentscope/mcp/*.py`

Related SOP: `docs/mcp_integration.md`