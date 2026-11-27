# Pre-execution validation

Use `ToolValidationMiddleware` to reject a proposed tool call before its function
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
length and rejection of extra fields. `ToolValidationMiddleware` checks the
final arguments against that schema without converting their types, then calls
the application's synchronous validator. Return `None` to permit execution or
a string (including an empty string) to deny it. Denials yield a `ToolChunk`
with `state=ToolResultState.DENIED` without calling the function. Tool outputs
and execution errors retain their normal behavior.

The library helper handles schema validation and rejection consistently across
tools. `validate_notification` contains only this application's policy; use
`functools.partial` to bind its trusted context:

```python
from functools import partial
from agentscope.tool import ToolValidationMiddleware

validation = ToolValidationMiddleware(partial(validate_notification, context))
```

The helper isolates nested business arguments from the caller and gives the
validator a separate copy. Validators must not rewrite it: modifications raise
`ValueError`, and execution does not occur. Other return types, including
awaitables, raise `TypeError`. Invalid tool schemas and validator exceptions
also propagate without dispatch; `Toolkit` reports ordinary exceptions using
its existing `ERROR` handling. Only argument-schema violations and returned
rejection reasons become `DENIED`.

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

`ToolValidationMiddleware` is a reusable gate for selected tools, while this
example supplies a concrete notification policy. It covers calls through
`await tool(...)` or `Toolkit.call_tool` when the middleware is registered last.
Calling `tool.call(...)` or the original Python function directly bypasses tool
middleware. Unwrapped tools, arbitrary Python/API calls, and externally executed
tools are outside this boundary. For state-injected tools, the library excludes
the internal `_agent_state` argument from business validation and passes the
original state object to the tool. This does not make caller-supplied state a
trusted identity: bind authority in the host validator's closure instead.

Identity is supplied by a trusted host, not authenticated by the middleware.
Validation occurs when the invocation stream is consumed, not when the
unconsumed stream is created. The time check gates admission; it does not revoke
an action already running or guarantee execution before a strict deadline.
Even synchronous code can cross a wall-clock boundary or be paused by the OS.
Nonces and persistent replay protection, if needed, belong to the application's
validator/executor. A tool that fails after starting may already have produced
a side effect; the middleware does not provide rollback.

The regression tests use a controlled clock to check exact expiry and a call
consumed after expiry. A deterministic scheduling test verifies that, in this
synchronous example, dispatch happens before an outer task can resume after the
policy check. Library tests also check that caller and retained validator
references cannot mutate nested checked inputs while an asynchronous tool waits.

Run the regression tests from the repository root:

```bash
python -m pytest tests/action_validation_example_test.py
```
