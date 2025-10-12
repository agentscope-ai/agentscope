# 总体 SOP 指南

本文件为仓库级别的标准作业规程（Standard Operating Procedure, SOP）总纲，所有子模块的 SOP（如 `docs/utils/SOP.md`）必须遵循并在执行前对齐。编写与修改代码前，请先阅读 `AGENTS.md` 及本文件。

## 核心原则
- **SOP 为第一真相**：任何功能的修改、修复、增删，都需先更新对应 SOP，再实现代码。代码仅是可再生的表达。
- **底层组织架构定位**：仓库提供骨架与交互逻辑供上层业务复用，不直接交付面向用户的最终功能。
- **计划优先**：在执行任何任务前，先形成清晰计划并与相关人员达成一致。
- **简洁组合**：延续 Unix 哲学与简单代码偏好，避免复杂、难维护的设计。

## 基础流程
1. **定位 SOP**：根据子系统找到对应文档（例如 `_utils` 对应 `docs/utils/SOP.md`）。若不存在，应先新增 SOP 模板并提交审阅。
2. **更新 SOP**：描述变更动机、影响范围、替代方案、接口兼容性等；保持结构化条目，方便审阅。
3. **编写 `todo.md`**：在仓库根目录补充执行步骤与验收清单，确保可追踪、可验证、可回滚。
4. **等待批准**：未经明确批准不得推进实现，保持计划与执行同步。
5. **实现与验收**：按 `todo.md` 执行，并依据验收清单提交测试/验证结果；所有涉及代码的任务必须在提交前运行 `ruff check src`（或 `pre-commit run --files $(git ls-files 'src/**')`）并清零告警。
6. **同步文档**：代码合入后同步更新相关 README、教程、示例、`CLAUDE.md` 等。

## 文档结构约定
- `docs/SOP.md`：全局总纲（本文件）。
- `docs/<模块名>/SOP.md`：模块级 SOP，需与目录结构保持一致，例如 `src/agentscope/_utils` → `docs/utils/SOP.md`。
- 若模块内有更细分功能，可在子目录追加 `SOP_<功能>.md`，但必须在模块级 SOP 引用，保持索引一致。

## 模块索引（按 src/agentscope/ 映射）
- agent → docs/agent/SOP.md
- model → docs/model/SOP.md
- formatter → docs/formatter/SOP.md
- tool → docs/tool/SOP.md
- memory → docs/memory/SOP.md
- rag → docs/rag/SOP.md
- session → docs/session/SOP.md
- pipeline → docs/pipeline/SOP.md
- tracing → docs/tracing/SOP.md
- evaluate → docs/evaluate/SOP.md
- mcp → docs/mcp/SOP.md
- embedding → docs/embedding/SOP.md
- token → docs/token/SOP.md
- message → docs/message/SOP.md
- plan → docs/plan/SOP.md
- types → docs/types/SOP.md
- exception → docs/exception/SOP.md
- hooks → docs/hooks/SOP.md
- module → docs/module/SOP.md

## 维护与审计
- 每次迭代前复查相关 SOP 是否最新；如发现实际实现与 SOP 不一致，必须优先调整 SOP 并回溯原因。
- 发布版本时，应汇总关键 SOP 变更并记录于 `docs/changelog.md`。
- 接受审计或交接时，以 SOP 作为对外说明依据，确保任何人都能依此快速理解与操作。

## 关联文档
- `AGENTS.md`：行为准则与 workflow 原则。
- `docs/utils/SOP.md` 等子模块 SOP。
- `todo.md`：执行步骤与验收清单（由当前任务维护）。
- `CLAUDE.md`：关键调用链与实现逻辑图谱。

> 备注：若子模块 SOP 出现明显偏差（未遵循本总纲、未及时更新、结构混乱），必须立即纠正并新增相应的验收项。
