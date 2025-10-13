allowed-tools: Edit, Bash(pytest:*), Bash(ruff check src*), Bash(pre-commit:*), MCP(filesystem:read-write)
description: Execute approved plan with implementer/tester sub-agents (LeetCode killer mindset: Efficient, optimized, boundary-focused). Scope via $ARGUMENTS.
argument-hint: [subtask focus, e.g., 'pipeline stream_printing_messages end_signal']
---

# /take-action: Execute Approved Plan with LeetCode Killer Mindset

**Preconditions**
- Plan (SOP_PATCH / PLAN_STEPS / TODO_BLOCK / ACCEPTANCE) has approval and prerequisites are complete.
- Target branch is `easy`; docs-first gating and SOP updates are already in place (handle externally if needed).
- Reference `AGENTS.md`, `CLAUDE.md`, and relevant `docs/<module>/SOP.md` before acting. `$ARGUMENTS` can narrow scope; otherwise execute the entire PLAN_STEPS.

## Workflow (sequential chunks; parallel tester if plan allows; LeetCode killer: Decompose like medium/hard problems - optimal time/space, edge cases first, clean code no extras)
Embed Vibe Checker (VeriCode IF for killer quality): Select 3–5 instructions (e.g., E501 lines<79 for readability, PLR0912 branches<=4 for simplicity, UP024 error handling for robustness, PTH pathlib for modern stdlib). After each chunk, run Bash ruff/pre-commit; compute IF (% pass) + composite vibe score (α=0.5 IF + 0.5 func cov, correlate human pref). Branch: If vibe>80%, proceed; else refine chunk (ULTRATHINK LeetCode-style: "Optimal alt? E.g., O(n) loop vs recursion; Test edges: Empty input, max size").

1. **Implementer** (Killer core: Problem-solve like LC hard - Break to O(1) helpers, dry-run mentally, implement clean/pass one edge at a time)
   - Apply planned code updates under `src/agentscope/**` per plan subtasks.
   - Mindset: Efficient (time/space first, e.g., async gather O(1) space); Boundary-first (e.g., empty queue, timeout=0); Clean (no magic nums, type hints, minimal deps).
   - Keep diffs minimal, cite SOP/CLAUDE sections, show via MCP Edit.
   - After each chunk: Run `ruff check src` (or `pre-commit run --files $(git ls-files 'src/**')` on touched) and compute VeriCode IF (e.g., grep AST for branches/docs). Vibe branch: If >80%, commit chunk; else killer-refine (e.g., "Branches=5>4: Refactor to loop+guard; Evidence: PLR0912; Alt: Hash map for O(1) lookup").
   - End chunk: Inline diff + vibe score (e.g., "IF 88%: Style clean, logic optimal O(n)").

2. **Tester** (Killer verify: LC test suite - Cover all cases (happy/edge/error), assert tight, no flakiness; Extend to vibe IF)
   - Implement/execute tests per ACCEPTANCE. Use deterministic stubs (e.g., mock VLM JSON edges: invalid image, timeout=0); Avoid external calls.
   - Coordinate with Implementer on failures; Re-run `pytest` until passing. Killer: Add boundary asserts (e.g., "Queue empty → No block"); Vibe extend: Test IF (e.g., mock for doc coverage, branch limits).
   - After tests: Composite score (func cov + IF %); If <80%, flag killer causes (e.g., "Bad vibe: Space leak in gather, evidence: Memory trace; Fix: Yield gen for O(1)").
   - Re-run until aligned (multi-round like LC debug).

3. **Verification gates** (Killer final: Triple-check like submit - Func + vibe + perf)
   - Confirm `ruff check src` and all `pytest` pass (no warnings). Add vibe gate: Overall IF>80% (no regression >5%, check U-shape pos bias).
   - If bad vibe, suggest killer refine (e.g., "Edge miss: Max agents=1000; Add assert").
   - Update `todo.md` completion if required.

4. **Merge outputs**
   - Summarize key diffs, test results inline (no extra files). Include killer vibe: "Overall score: 94% (IF 90%, func 98%); Human pref: High (optimal async, no anti-patterns)."
