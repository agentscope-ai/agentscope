You are an expert coding agent. IMPORTANT RULE - CONTEXT MANAGEMENT:
- NEVER auto-compact, summarize, prune, or compress the conversation history automatically.
- Do NOT trigger compaction unless the remaining context is BELOW 25% (or <30k tokens left, whichever is stricter).
- If compaction is ever needed, ALWAYS ask for user confirmation first with "/compact" command.
- Preserve ALL previous messages, tool outputs, file contents, decisions, and code changes exactly as-is until explicitly told otherwise.
- If you detect the system trying to compact early, ignore it and continue with full history.

# Agents Guide (Jump)

权威口径（必须长期为真）：
- **功能/模块规范唯一源是 SOP**：以 `docs/**/SOP.md` 为唯一权威来源（总纲为 `docs/SOP.md`，模块级为 `docs/<module>/SOP.md`）。
- `specs/###-*/{spec,plan,tasks}.md`：变更河流（开发过程与验收），不作为规范源。
- 本文件仅作为 agent 运行约束与导航索引；不得复述或扩展功能/模块契约（避免与 SOP 打架）。

- 全局总纲：请阅读 `docs/SOP.md`。
- 模块级规范：见 `docs/<module>/SOP.md`。
- 程序记忆：以各模块 `docs/<module>/SOP.md` 的“文件映射/调用链”章节为准（不维护额外的“程序记忆”文档体系）。
- 变更河流：`specs/###-*/{spec,plan,tasks}.md` 用于迭代开发与验收，不作为规范源。

## Active Technologies
- Python 3.10 (per `setup.py` python_requires) + Managed in `setup.py` (install_requires + extras) (002-version-deps-sync)

## Recent Changes
- 002-version-deps-sync: Added Python 3.10 (per `setup.py` python_requires) + Managed in `setup.py` (install_requires + extras)
