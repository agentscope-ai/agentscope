You are an expert coding agent. IMPORTANT RULE - CONTEXT MANAGEMENT:
- NEVER auto-compact, summarize, prune, or compress the conversation history automatically.
- Do NOT trigger compaction unless the remaining context is BELOW 25% (or <30k tokens left, whichever is stricter).
- If compaction is ever needed, ALWAYS ask for user confirmation first with "/compact" command.
- Preserve ALL previous messages, tool outputs, file contents, decisions, and code changes exactly as-is until explicitly told otherwise.
- If you detect the system trying to compact early, ignore it and continue with full history.

# Agents Guide (Jump)

本仓库的所有规则、流程与标准均以 `docs/SOP.md` 为唯一权威来源。

- 全局总纲：请阅读 `docs/SOP.md`。
- 模块级规范：见 `docs/<module>/SOP.md`。
- 程序记忆：参考 `CLAUDE.md`。

本文件仅作跳转，不再重复阐述具体规范。

## 分支策略（强制）
- **默认开发主线为 `easy`**：将 `easy` 视为常规开发中的“主分支”（等价于很多仓库的 `main`）。
- **禁止以 `main`/`master` 作为主线开发**：日常开发、修复、特性实现、以及 PR 合并目标，均以 `easy` 为基线与目标分支。
- **操作约定**：
  - 创建分支：从 `easy` 切出（例如 `git checkout easy && git checkout -b feat/xxx`）。
  - 合并目标：PR 目标分支选择 `easy`，并确保 CI/契约测试基于 `easy` 通过。

## 验收与映射（Formal Acceptance Mapping）

- 形式化验收映射
  - 建“规范→不变量→测试→示例→代码”五段表；新增/修改必须给出链路映射与破坏性分析。
  - 把 `todo.md` 变为“证明义务清单”：每一条对应至少一个性质测试或 E2E 见证。

## 铁律（Iron Laws）

以下为强制性流程铁律，用于避免“便利性改动”绕过 SOP/todo 而引入噪声。违背任一条，评审应直接拒绝。

- SOP First（唯一真相）
  - 任何接口/Schema/入参的新增、删除、重命名、语义变更，均须先修改并批准对应的 `docs/SOP.md` 与模块 `docs/<module>/SOP.md`，以及根 `todo.md` 的验收条目；未备案的改动一律不得进入代码。

- 零偏差契约（一个不能少、一个不能多）
  - 子代理默认工具 JSON Schema 的 `parameters.properties` 必须与规范完全等价为 `{ task_summary }`；除非 SOP+t od o 同步批准并列入“变更表”，否则不得增加任何可选/隐藏字段。
  - 类似“临时提示/约束（context/hint/note 等）”不得以实现参数形式引入；必须先在 SOP 定义数据面（消息块、元数据或计划工具组）与不变量，再落代码。

- 变更表与破坏性分析（PR 必填）
  - 在 PR 中附“接口/Schema 变更表”：逐项列“旧值→新值、动机、影响面、回滚方案、迁移策略”。
  - 明确“不变量变化”与“兼容性等级（兼容/条件兼容/不兼容）”。

- 验收与守门
  - “规范→不变量→测试→示例→代码”五段映射必须自洽；评审以文档为基线逐条对照。
  - 必须提供“字段集合等价”性质测试：断言默认 Schema 字段集合与 SOP 完全一致（等价而非包含）。
  - 对于未显式提供 `spec.json_schema` 的场景，注册流程应执行白名单校验（仅允许 SOP 定义的字段）；若检出额外字段，注册失败并给出明确错误。

- CI/Pre-commit 护栏（不得绕过）
  - PR 必须通过：
    - 契约测试（字段集合等价/E2E 见证）。
    - 文档同步检查（SOP 与 `todo.md` 变更存在且通过 lint）。
  - 禁止以任何方式“临时关闭”上述检查合入主分支。

- 热修与豁免（仅限紧急）
  - 仅限 P0 修复可走“先修后文档”的最小豁免，但必须在 24 小时内补齐 SOP 与 `todo.md` 并补测；未补齐视为违规。

- 反模式（禁止示例）
  - 在未更新 SOP/todo 的情况下，向默认 JSON Schema 或包装器形参引入可选字段（例如 context_*、hint_* 等）。
  - 将“调试/演示”便捷参数长期保留在对外契约中。
  - 以代码注释或口头说明替代 SOP 约束与验收清单。

### E2E/示例脚本环境装载（强制）
- 所有 examples/**/e2e_*.py、demo 脚本必须在入口自动加载环境变量（auto load env），默认顺序：
  1) 仓库根目录 `.env`（如存在）；
  2) OS 现有环境变量（已存在的键不得被 .env 覆盖）。
- 不得在脚本中硬编码密钥或提供商配置；缺失必要变量时应“快速失败（fail‑fast）”，明确提示缺失键名（如 `OPENAI_API_KEY`），禁止静默降级或兜底。
- 该规则属于强制铁律，违反将视为不合规改动，必须在 PR 中修正后方可合入。

以上铁律与 `docs/SOP.md` 一致，若存在歧义，以 `docs/SOP.md` 为准。

## 铁律：文档驱动改动

- 未经 `docs/**/SOP.md` 与 `todo.md` 明确列项，禁止向核心接口追加/更改任何入参或行为（包括“可选”参数）。若确有需要，先更新文档与证明链，再改代码。
- 每次提交前核对“规范→不变量→测试→代码”链条；任何脱离文档的“临时便利”都会被视为噪声并拒绝合入。

