allowed-tools: Bash(pytest:*), Bash(ruff check src*), Bash(mypy src*), Bash(git:*), Read
description: Verify gates (pytest/ruff/mypy), then Review + Optimize. Branch GOOD/BAD; align with Docs-first & easy-only.
argument-hint: [focus, e.g., 'pipeline stream_printing_messages end_signal']
---

# /verify-optimize: Verify → Review → Optimize (Closed Loop)

**Preconditions**
- Approved plan completed via `/take-action`; target branch is `easy`.
- All relevant artifacts and summaries (Implementer/Tester/Documenter/Verifier/Reviewer) are available.
- Reference `AGENTS.md`, `CLAUDE.md`, and relevant `docs/<module>/SOP.md`. `$ARGUMENTS` can narrow scope; default verifies all changes.

## Workflow
1. **Verify**
   - Invoke the verifier sub-agent to run planned `pytest` commands and confirm `ruff check src` (no warnings) and `mypy src` results.
2. **Branch on results**
   - **GOOD CASE** (pytest, ruff, mypy all pass):
     - Call the reviewer sub-agent to produce the PR summary for the `easy` branch.
     - Call the optimizer sub-agent to capture lessons and recommend CLAUDE/SOP refinements and next-task seeds.
     - Output format: `GOOD: Validated. PR ready; Optimized: <lessons>. Next: <seeds>.`
   - **BAD CASE** (any gate fails):
     - Use verifier analysis to map failures to specific contracts (ReAct, Toolkit, Formatter, Model, Pipeline, MsgHub, Tracing, Docs-first).
     - Provide non-code remediation guidance (rollback points, minimal patches, extra tests/docs).
     - Output format: `BAD: Causes analyzed. Propose fixes and re-run Take Action.`
3. **Merge summary**
   - In the main thread, consolidate conclusions and advise next steps (approve, iterate, or reopen `/plan-with-agents`).

End with: `Branch: GOOD/BAD. Your input on fixes/next? (SOP-first, easy-only)`
