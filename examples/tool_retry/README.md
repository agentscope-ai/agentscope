# Bounded tool retries

This offline example shows where a tool retry belongs and when it should
stop. A local lookup deliberately fails before returning a fixed status.
`ReadOnlyRetryMiddleware` wraps a `FunctionTool`, and the real `Toolkit`
dispatches it and accumulates the final result. No model, API key, network
service, or example-specific dependency is needed.

## Run

Use Python 3.11 or later. From the repository root, install AgentScope in
your environment and run:

```bash
pip install -e .
python examples/tool_retry/main.py
```

Expected output:

```text
baseline: attempts=1, state=error, output=lookup unavailable (1)
recovered: attempts=3, state=success, output=status: available
exhausted: attempts=3, state=error, output=lookup unavailable (3)
```

Each scenario starts with a fresh lookup. The baseline has no middleware.
The recovered lookup fails twice and succeeds on its third attempt. The
exhausted lookup fails on every allowed attempt; `Toolkit` converts the
last exception into an error result so its caller can inspect it.

## Retry boundaries

The policy makes at most three attempts, including the initial call. It
retries only `TransientLookupError`, only for a tool declared
`is_read_only=True`, and only before the tool has yielded its first chunk.
Other exceptions propagate. Cancellation also propagates without retry;
when dispatched through `Toolkit`, cancellation becomes an interrupted
tool result according to Toolkit's existing behavior. A transient failure
after partial output is preserved as an error rather than replaying output.
Returned error chunks are not retried.

The read-only flag is trusted tool metadata. It does not enforce the
absence of side effects. Apply a retry policy only after checking the
operation's semantics. This small fixture uses immediate attempts so its
behavior is deterministic; a remote service needs its own error
classification and delay policy. This example is not a general network
retry implementation or a guarantee of exactly-once execution.

Tool middleware wraps the tool's `call()` execution. Agent-level
`MiddlewareBase.on_acting` wraps a broader step, including permission
handling. Model retry settings govern model requests, separately from
tool retries. See the [tool middleware documentation](https://github.com/agentscope-ai/docs/blob/main/en/versions/2.0.8/building-blocks/tool/python-tool.mdx)
for the wrapping interface.

## Verification

With the repository's development dependencies installed:

```bash
pytest tests/tool_retry_example_test.py
```

The tests cover recovery, exhaustion, permanent errors, a tool declared
non-read-only, partial output, and cancellation. An integration test uses
the existing scripted `MockModel` with a real `Agent.reply_stream` to
check tool-result events and saved context for recovery and exhaustion.
The scripted model makes no LLM request and does not demonstrate an LLM
reasoning about failures.
