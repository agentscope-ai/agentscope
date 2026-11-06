# Pre-execution validation

Use `ToolMiddlewareBase` to reject a proposed tool call before its function
executes. This offline example checks the final argument schema, the action,
the allowed recipient, and a host-provided agent/workflow context with an expiry.
Only accepted calls append to a local list. No model, mail server, API key, or
extra dependency is required.

From the repository root, install AgentScope and run the example:

```bash
pip install -e .
python examples/action_validation/main.py
```

Expected output:

```text
allowed: Notification recorded.
blocked recipient: The recipient is not authorized.
invalid arguments: The final tool arguments do not match the schema.
Notifications recorded: 1
```

## How the gate works

`NotificationParams` defines the accepted argument schema, including message
length and rejection of extra fields. The middleware uses `jsonschema` to check
the final arguments without converting their types. A denied call yields a
`ToolChunk` with `state=ToolResultState.DENIED` and returns without calling
`next_handler`. An accepted call forwards the original arguments and tool output.

The host creates `ExecutionContext` outside the model arguments and binds it to
one agent/workflow's tool instance. This example permits only `support-agent`
in `ticket-42`, the `send_notification` action, and `support@example.com` before
the context expires. `valid_until` must be a timezone-aware `datetime`. In an
application, derive this context and the policy from trusted host state; model
arguments must never supply identity or grant authority. Do not share a tool
instance carrying one workflow's authority across other workflows.

Register the validation middleware **last**, after any middleware that rewrites
arguments, so it checks what the function will receive. AgentScope's earlier
agent-side schema validation cannot check subsequent middleware rewrites.
This ordering protects the wrapped function; an outer middleware must not
perform the protected action itself before delegation.

You can register the same wrapped `FunctionTool` with a `Toolkit` used by an
`Agent`. Normal agent permission checks still apply. The demo calls the tool
directly to make the validation outcome deterministic without an LLM; direct
calls do not run the agent permission engine.

## Scope

This is a concrete application policy, not a new AgentScope security API or
system-wide enforcement layer. It covers the selected, non-state-injected
`FunctionTool` when invoked through `await tool(...)` or `Toolkit.call_tool`.
Calling `tool.call(...)` or the original Python function directly bypasses tool
middleware. Unwrapped tools, arbitrary Python/API calls, and externally executed
tools are outside this example's boundary.

Identity is supplied by a trusted host, not authenticated by the middleware.
The time check only gates the start of execution; it does not revoke an action
already running. Nonces, persistent replay protection, distributed coordination,
and rollback after tool failures are not implemented. A tool that fails after
starting may already have produced a side effect. These broader requirements
remain open in [#1509](https://github.com/agentscope-ai/agentscope/issues/1509).

Run the regression tests from the repository root:

```bash
python -m pytest tests/action_validation_example_test.py
```
