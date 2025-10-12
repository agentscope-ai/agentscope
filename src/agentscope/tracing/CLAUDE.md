```
Module: `src/agentscope/tracing`
Responsibility: OpenTelemetry-based tracing for LLM, tools, agents and formatters.
Key Types: `Tracer`, `Span`, `TraceConfig`

Key Functions/Methods
- `setup_tracing(endpoint, service_name="agentscope") — configures tracing infrastructure
  - Inputs: Tracing endpoint URL, service metadata
  - Returns: Configuration status with connection details
  - Side‑effects: Network connections to tracing platforms
  - References: `src/agentscope/tracing/__init__.py`