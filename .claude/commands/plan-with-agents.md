---
allowed-tools: Bash(git status:*), Bash(ruff check src*), Bash(mypy src*), Bash(pytest:*), Bash(pre-commit:*), MCP(filesystem:read-only)
description: Docs-first planning chain for AgentScope-easy. Understand context, align SOP/CLAUDE, produce executable plan before any code runs.
argument-hint: [task-description, e.g., 'Add Qdrant pruning option to rag store']
---

# /plan-with-agents · Docs-First Plan Mode for AgentScope-easy

**Important:** This command only plans. No code edits, installs, or write operations. Reference `AGENTS.md`, `docs/SOP.md`, `docs/<module>/SOP.md`, and `CLAUDE.md` in every step. All plans must respect the "文档 → 计划 → 执行 → 验收" pipeline和“`src/` 是 SOP 的可再生表达”。分支策略：仅限 `easy` 分支（禁止面向 `main` 的任何合并/提交）。

## Workflow (Sequential Sub-Agents)
Use `$ARGUMENTS` as the requested task (e.g., `"Add Qdrant pruning option to rag store"`).

### 1. Understander Sub-Agent — Map Current State
- Actions (read-only):
  - Load `AGENTS.md`, `docs/SOP.md`, relevant `docs/<module>/SOP.md`, `CLAUDE.md`, and any nearby README/CHANGELOG entries.
  - If helpful, inspect `src/agentscope/<module>` and corresponding tests under `tests/` using MCP filesystem.
  - Summarize as UNDERSTANDER_SUMMARY（内联输出，≤1500 tokens）包含：
    - Project stance: Docs-first, easy-branch only, SOP tree, Ruff/mypy/pytest gates.
    - Relevant modules & responsibilities (e.g., rag store pipeline, toolkit flow).
    - Existing constraints, risks, open questions, and any SOP gaps.
  - Required approvals: SOP update? `todo.md` checklist? CLAUDE.md alignment?
- Output must explicitly cite which SOP sections/clauses apply to the task,以内联块形式返回（不写文件）。
- Await manual review before proceeding.

### 2. Planner Sub-Agent — Draft Docs-First Execution Plan
- Inputs: `$ARGUMENTS`, UNDERSTANDER_SUMMARY, CLAUDE.md, the relevant SOP excerpts.
- Behaviors:
  - Enter deliberative (ULTRATHINK) mode but stay within the project’s rules.
  - Produce以内联命名块：
    - SOP_PATCH：拟修改的 `docs/<module>/SOP.md` 片段（Before/Change/After 或 diff）。
    - TODO_BLOCK：根目录 `todo.md` 的执行步骤（5–10 步）、回滚提示、输出物（tests/docs/examples）。
    - ACCEPTANCE：与 SOP 对齐的验收清单（ruff check src、mypy src、pytest 目标、文档同步、CLAUDE.md 更新点）。
    - RISKS：主要风险与缓解；ALTERNATIVES：可选方案或分阶段路径。
- Ensure the plan keeps code changes scoped to `src/agentscope/<module>` and tests, with no business-logic leakage into core.
- Await manual confirmation before execution.

### 3. Merge & Prompt Main Thread
- Consolidate into the conversation, highlighting:
  - Key findings from understander-summary.
  - Proposed subtasks and dependencies from plan.md.
  - Required approvals (SOP updates + todo.md) before coding can begin.
- End with: `Ready to refine or approve plan (easy‑only, SOP‑first). Next: Create todo.md entries?`

> Reminder: This slash command stops after planning. Implementation must be manually triggered only after SOP and todo.md are updated and approved.
