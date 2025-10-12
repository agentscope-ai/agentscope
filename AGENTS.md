# Repository Guidelines

> 本文为仓库级运行手册，供人类与智能体共同遵循。逻辑固定、实现灵活：严格执行约束与流程，代码细节可按需发挥，但必须满足本文与对应 SOP 的要求。

## 🚨 Critical Rules
- **Docs-first 永远优先**：任何功能的新增/修改/删除，先更新对应 SOP（`docs/SOP.md` 或 `docs/<module>/SOP.md`），再调整 `todo.md`，获批后才能触及 `src/` 代码。
- **底层架构定位**：`src/` 只提供可组合的骨架和交互逻辑，不接受直接面向业务的特性；如需业务行为，请放到独立插件/MCP 工具/`examples/`。
- **分支单一**：所有工作基于 `easy`；`main` 为只读上游参考，禁止向 `main` 推送或合并。
- **计划先于执行**：每个任务先写计划（可简化为 3~5 步），保持单步 in-progress。执行中若范围改变，先更新计划和 SOP，再继续。
- **Ruff 仅检测但必须运行**：完成任何代码改动后立即运行 `ruff check src`（或 `pre-commit run --files $(git ls-files 'src/**')`）。CI 中 Ruff 为非阻断（`--exit-zero`），但在合并前必须清零告警，由代码评审严格把关。
- **类型与异常可追踪**：公共接口必须带类型标注；禁止裸 `except` 或吞掉异常，统一使用 `agentscope._logging.logger` 记录。
- **CLAUDE.md 必须引用**：执行任务前引用 CLAUDE.md（项目程序记忆），重要流程变更同步更新 CLAUDE.md 与 SOP。

## 🎯 Project Context
- **定位**：AgentScope-easy 是 AgentScope 框架的“底层组织架构”分支，聚焦多 Agent 编排、模型/工具/记忆/管线等基础骨架。我们固定运行逻辑（消息流、Hook、工具协同等），允许根据 SOP 自由实现细节。
- **模块视图**：
  - `agent/` 智能体基类、ReAct 实现、用户交互入口。
  - `model/` 第三方 LLM 适配（OpenAI、DashScope、Ollama、Gemini 等）。
  - `tool/` 工具注册、MCP 集成、多模态工具。
  - `memory/` 短期/长期记忆，含 mem0 接口。
  - `pipeline/` 顺序与扇出协同、MsgHub 广播。
  - `rag/` 文档解析、向量存储抽象。
  - `tracing/` OpenTelemetry 追踪。
  - 详见 `docs/<module>/SOP.md` 中的职责/接口说明。
- **SOP Tree**：
  - 全局纲领：`docs/SOP.md`。
  - 模块 SOP：`docs/<module>/SOP.md`（名称与 `src/agentscope/<module>` 对应，去掉前缀 `_`）。
  - 每次改动 `src/.../<module>` 必须同步更新对应 SOP（或在 PR 中写明无需更新的理由）。
- **运行逻辑固定**：消息流、Hook 顺序、工具调用协议、计划/记忆交互等必须遵循 SOP 描述；代码实现可自由发挥，但禁止改变这些流程约束。

## 🔧 Coding Norms & Conventions
- **风格**：PEP 8、4 空格缩进、行宽 79；公开 API 必须添加类型注解与 Google 风格 docstring。
- **命名**：
  - 模块/文件：小写 + 下划线（例 `_toolkit.py`）。
  - 类：`PascalCase`；内部基类可 `_` 前缀。
  - 常量：`UPPER_CASE`；内部帮助函数 `_snake_case`。
- **结构**：保持模块职责单一，优先组合而非继承；新增功能先尝试复用现有模块（model/formatter/toolkit/memory/rag/pipeline）。
- **异步与 I/O**：避免阻塞调用；提供 `timeout`、重试或向上暴露参数；处理取消（`asyncio.CancelledError`）。
- **错误处理**：使用明确异常（`ValueError`/`RuntimeError` 等）；记录日志后抛出；禁止裸 `except` 或静默失败。
- **Ruff & pre-commit**：
  - 开发中可单独运行 `ruff check src`；
  - 提交前执行 `pre-commit run --files $(git ls-files 'src/**')`（激活 black、ruff、flake8、pylint、mypy 等）；
  - CI 中 Ruff 仅检测（不阻断），但在合并前必须清零 Ruff 告警（评审口径为“零告警”或明确豁免说明）。

## 🐝 Agent SOP & Workflow
- **准备阶段**：
  1. 阅读相关模块 SOP 与 CLAUDE.md；在 `todo.md` 写下执行步骤 + 验收清单（最小、可验证、可回滚）。
  2. 获取批准后才能进入执行阶段。
- **执行阶段**：
  - 代码实现仅限 `src/`；遵循“骨架可再生”的理念，调整逻辑需先更新 SOP 描述；
  - 保持 diffs 精准、函数签名清晰；适时补充最小示例或单测；
  - 每完成一小步都运行 `ruff check src`（或等效 pre-commit）；
  - 若范围变化，立即回到 SOP & todo 更新并重新审批。
- **收尾阶段**：
  - 在 PR/任务描述中解释“为何这样做”与替代方案（执行原则 3）。
  - 运行全部相关测试；同步更新示例、READMEs、CLAUDE.md。
  - 审核时仅接受干净历史（rebase/FF），禁止混入无关改动。

## 🧪 Testing & Verification
- **测试目录**：`tests/` 中按模块划分（如 `tests/tool_test.py`）。新增/变更逻辑必须附带单测或可验证示例。
- **核心命令**：
  - 全量：`pytest`
  - 单模块：`pytest tests/<target>_test.py`
  - 类型：`mypy src`
  - 静态：`ruff check src`（CI 不阻断；合并前需零告警或提供豁免说明）
- **覆盖范围**：优先覆盖交互逻辑、Hook、异常处理、工具调用路径；多模态/网络调用使用 stub 或 in-memory 替代（参见 `tests/rag_store_test.py` 等）。
- **验收清单**（最小）：
  - 文档（SOP、CLAUDE.md、教程）已更新；
  - 代码通过 Ruff/mypy/pytest；
  - `todo.md` 项全部勾选，留存测试证据。

## 🚀 Deployment & CI
- **本地脚手架**：`python -m pip install -e .[dev]` 安装开发依赖。
- **CI 要求**：
  - pre-commit 钩子执行 black、ruff（仅检测）、flake8、pylint、mypy、pyroma；
  - `pytest`、`mypy` 需成功；Ruff 在 CI 中仅报告但不阻断，合并前需零告警（或在 PR 中记录豁免理由）。
- **发布节奏**：版本号位于 `src/agentscope/_version.py`；变更需在 `docs/changelog.md` 与相关 SOP 中记录。
- **依赖策略**：启用 extras（`.[full]`、`.[dev]`）覆盖可选模型/RAG/评测；运行环境要求 Python ≥ 3.10。

## 💡 Best Practices & References
- **组合胜于扩展**：优先使用现有模型/工具/记忆/RAG/管线，自建模块需明确缺口与边界。
- **渐进式抽象**：发现重复后再提炼公共组件，避免预先设计复杂框架。
- **CLAUDE.md 使用**：
  - 每次任务开头 prompt：`Reference CLAUDE.md strictly`；
  - 关键调用链写入 CLAUDE.md，对应实现补充 `docs/<module>/SOP.md`。
- **知识同步**：文档、代码、示例、SOP、CLAUDE.md 必须保持一致；发现偏差先纠偏文档，再动代码。
- **学习资源**：
  - AgentScope 官方教程：https://doc.agentscope.io/
  - OpenTelemetry、MCP 标准文档；
  - 项目内 examples/ 作为行为参考。

> 最终提醒：此仓库的一切改动必须沿着“文档 → 计划 → 执行 → 验收”的链路推进。SOP 是灵魂，CLAUDE.md 是程序记忆，`src/` 只是可再生的表达。
