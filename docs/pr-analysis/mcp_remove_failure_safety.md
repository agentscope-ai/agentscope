# PR draft: preserve sandbox MCP state when deregistration fails

## Review checklist

- [x] Document the gateway source of the missing-registration response.
- [x] Accept additional response fields while retaining scoped detail matching.
- [x] Document propagation of underlying request exceptions and strict-close behavior.
- [x] Cover missing-registration and unrelated 404 responses with extra fields.
- [x] Describe sandbox scope, REST behavior, and remaining limitations.
- [x] Run the related tests and pre-commit checks.

## Title

fix(workspace): preserve sandbox MCP state when deregistration fails

## Description

When Gateway deregistration fails, sandbox `remove_mcp()` previously discarded the local instance and persisted declaration and returned normally. A subsequent add with the same name and scope could then fail with HTTP 409 because the Gateway still held the registration.

Explicit sandbox removal now closes the connected Gateway client with `ignore_errors=False` before removing local state. Gateway HTTP and request failures propagate while preserving the instance, connection flag, declarations, and persisted file for retry. This applies to both stateful and stateless MCPs. Bulk cleanup remains best-effort, and clients already closed successfully can still be removed locally.

A 404 is accepted only when its `detail` identifies the missing registration for the exact MCP name, agent, and session. This permits retry after a successful remote deletion whose response was lost. Additional response fields are allowed; generic route/proxy 404s remain errors. Tests use the real Gateway app's response to verify the matching contract.

Follow-up to [#2773](https://github.com/agentscope-ai/agentscope/issues/2773) and [#2777](https://github.com/agentscope-ai/agentscope/pull/2777): the merged change addressed stateless clients being skipped, but did not preserve local state on deregistration failure.

## API impact and scope

- This guarantee concerns Gateway deregistration failures in sandbox workspaces. `LocalWorkspace.remove_mcp()` retains its existing cleanup behavior.
- `DELETE /workspace/mcp/{mcp_name}` still returns 204 on success. Deregistration exceptions now reach the app's default 500 response instead of being swallowed. This change does not add structured REST error mapping.
- The example frontend displays API errors and refreshes the MCP list only after a successful removal. Its delete dialog remains open and restores the button after failure; the existing uncaught Promise rejection in that dialog is a separate UI follow-up.
- Persistence remains best-effort: if saving `.mcp` fails after successful deregistration, the in-memory state can differ from disk. This change does not provide transactional persistence.
- Scoped 404 matching still depends on the Gateway's human-readable detail. A stable structured error code, including compatibility with older Gateways, is a separate protocol improvement.

## Validation

Validated in the `agentscope2` environment: 102 tests and 41 subtests passed. Seven live Daytona tests were skipped because `DAYTONA_API_KEY` was not set. All applicable pre-commit hooks passed.

Commands:

```bash
PYTHONPATH=src python -m pytest tests/workspace_mcp_remove_test.py tests/workspace_daytona_test.py tests/workspace_base_test.py tests/workspace_local_test.py tests/mcp_runtime_headers_test.py -q -rs
python -m pre_commit run --files src/agentscope/workspace/_sandboxed_base.py src/agentscope/workspace/_gateway_client.py tests/workspace_mcp_remove_test.py docs/pr-analysis/mcp_remove_failure_safety.md
```
