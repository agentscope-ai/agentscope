allowed-tools: Edit, Bash(pytest:*), Bash(ruff check src*), Bash(mypy src*), Bash(pre-commit:*), MCP(filesystem:read-write)
description: Execute approved plan using implementer/tester/documenter sub-agents (Docs-first, easy-only). Scope via $ARGUMENTS.
argument-hint: [subtask focus, e.g., 'pipeline stream_printing_messages end_signal']
---

# /take-action: Execute Approved Plan with Sub-Agents

**Preconditions**
- Plan (SOP_PATCH / PLAN_STEPS / TODO_BLOCK / ACCEPTANCE) has approval and prerequisites are complete.
- Target branch is `easy`; docs-first gating and SOP updates are already in place.
- Reference `AGENTS.md`, `CLAUDE.md`, and relevant `docs/<module>/SOP.md` before acting. `$ARGUMENTS` can narrow scope; otherwise execute the entire PLAN_STEPS.

## Workflow (coordinate sequentially; parallel only if the plan allows)
1. **Implementer**
   - Apply planned code updates under `src/agentscope/**`, with matching tests/examples per plan.
   - Keep diffs minimal, cite SOP/CLAUDE sections, and show changes via MCP Edit.
   - After each chunk run `ruff check src` (or `pre-commit run --files $(git ls-files 'src/**')` constrained to touched files) and the designated `pytest` targets; run `mypy src` when required.
2. **Tester**
   - Implement and execute tests according to ACCEPTANCE. Use deterministic stubs; avoid external network calls.
   - Coordinate with Implementer on failures; re-run `pytest` until passing.
3. **Documenter**
   - Update `docs/SOP.md`, module SOPs, CLAUDE.md, README/examples per SOP_PATCH.
   - Ensure documentation reflects new runtime logic and maintains SOP tree mapping.
4. **Verification gates**
   - Confirm `ruff check src`, `mypy src`, and all planned `pytest` suites pass with no outstanding warnings.
   - Update `todo.md` completion status if required and ensure documentation changes are committed.
5. **Merge outputs**
   - Summarize key diffs, test results, and doc updates inline (no extra files).
   - Provide suggested commit message / PR summary referencing relevant SOP and CLAUDE entries.

End with: `Ready for review/merge (SOP-first, easy-only). Repeat loop?`
