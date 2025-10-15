---
allowed-tools: Bash(pytest:*), Bash(ruff:* or pylint:*), Bash(git:*), Edit, Read
description: Full closed-loop: Verify func + vibe IF (VeriCode-inspired); Good: Optimize; Bad: Analyze causes (func + non-func).
argument-hint: [focus, e.g., 'Async VLM changes']
---

# /verify-optimize: Vibe-Aligned Closed-Loop with VeriCode IF Eval

IMPORTANT: Reference CLAUDE.md/plan.md/all *-md outputs. Focus: $ARGUMENTS (default: full). Embed VeriCode taxonomy (30 instructions: style/logic/docs/error/library) for vibe quality.

Workflow:
1. Parallel Verify + Vibe Eval:
   - Use verifier subagent for func tests (pytest cov>90%, async/VLM edges).
   - In parallel, use judge subagent for LLM-eval (func score 0-100%).
   - In parallel, embed VeriCode IF: Select 3-5 relevant instructions from taxonomy (e.g., style: E501 line_length=79; logic: PLR0912 max_branches=4; docs: D Google format; error: UP024 OSError; library: PTH pathlib). Run Bash "ruff check --select E501,PLR0912,D,UP024,PTH" or pylint on *-changes.md. Compute IF (instruction-level avg %, task-level binary pass). Composite vibe score: alpha=0.5 IF + 0.5 func (correlate human pref per LMArena).
   - Output: vibe-report.md (table: Instruction | Pass/Fail | Evidence/Score; Overall vibe: X%; Regression: Y% drop).

2. Branch on Results (func pass + vibe>80%):
   - **GOOD CASE** (pass + high vibe/IF): Sequential - Use reviewer subagent for PR summary/diff (git diff, compliance incl. IF). Then use optimizer subagent to extract lessons (e.g., "IF strong: Add 'Max branches=4' to CLAUDE.md"), refine CLAUDE.md, outline next plan.md (multi-round refine if needed). Output: "GOOD Vibe: Score 92% (IF 88%, func 96%); PR ready; Optimized: [feedback]. Next: [outline]. Human pref: High alignment."
   - **BAD CASE** (fail or vibe<80%): Sequential - Use debugger subagent (or verifier) for root causes (func + non-func, e.g., "Func cause: Timeout fail; Evidence: pytest log; Non-func: Style dev: Lines>79, evidence: Ruff E501; Vibe drop 15% - U-shape mid-pos low IF"). Explain fix path (e.g., "Refactor branches; Re-run ruff"). Output: "BAD Vibe: Causes explained (IF 62%); Re-take-action on Subtask X?"

3. Merge: Final summary. "Vibe loop complete. Regression detected? Approve merge/loop?"

End with: "Branch: GOOD/BAD. Tweak IF threshold?"
