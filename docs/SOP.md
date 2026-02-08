# 总体 SOP 指南

本文件为仓库级别的标准作业规程（Standard Operating Procedure, SOP）总纲，所有子模块的 SOP（如 `docs/utils/SOP.md`）必须遵循并在执行前对齐。编写与修改代码前，请先阅读 `AGENTS.md` 及本文件。

## 模块 SOP 模板（强制）
为统一各模块文档质量与结构，所有 `docs/<module>/SOP.md` 必须严格包含并按顺序编排以下五个一级标题。缺失任一项均视为不合规，需在 PR 中补齐或给出明确豁免理由。

1. **一、功能定义（Scope/非目标）**
   - 说明模块职责边界、设计目标以及明确的“非目标”，避免职责漂移。
   - 可补充背景约束、依赖前提或运行环境假设。
   - 子结构（必须按序出现）：
     1) 设计思路和逻辑：问题抽象、输入/输出、核心约束；避免口号化。
     2) 架构设计：分层/组件关系与边界（建议配 1 张简图）。
     3) 核心组件逻辑：关键流程/状态流转/约束，锚定到代码入口（文件/函数）。
     4) 关键设计模式：仅写已在代码中实现的模式（如 Hook/策略/观察者）。
     5) 其他组件的交互：与 Agent/Model/Tool/Session 等的调用契约与前后置条件。
   - 图表（强烈推荐）：Mermaid 或 ASCII 最小示意，例如：
     ```mermaid
     graph TD
       A[输入] --> B{判定}
       B -->|是| C[动作1]
       B -->|否| D[动作2]
     ```

2. **二、文件/类/函数/成员变量映射到 src 路径**
   - 将 `src/agentscope/<module>` 下的关键文件逐一列出，并说明各类/函数/成员的职责、关键逻辑或注意事项。
   - 内部基类/实验性实现需显式标注“内部使用”或“实验性”。

3. **三、关键数据结构与对外接口（含类型/返回约束）**
   - 罗列模块暴露的消息块、Pydantic 模型、TypedDict、枚举等数据结构，解释字段语义与约束。
   - 对外 API 必须给出签名、类型、返回约束、异常行为、并发/流式特性（例如是否返回生成器、是否支持取消）。

4. **四、与其他模块交互（调用链与责任边界）**
   - 描述典型调用链：上游/下游是谁、消息如何流转、各自的责任边界。
   - 必要时可给出简要的 ASCII 流程图，并在本 SOP/对应模块 SOP 中补齐跨模块图谱。
   - 标明与第三方系统（Studio、MCP、向量库等）的协议或约束。

5. **五、测试文件**
   - 列出与本模块绑定的测试文件路径（如 `tests/<module>_test.py`）与主要覆盖点。
   - 如存在未覆盖或尚未实现的约束，请在此标注并提出补测建议。

> **快速粘贴模板**（可按需增减二级标题）：
> ```markdown
> # SOP：src/agentscope/<module> 模块
>
> ## 一、功能定义（Scope/非目标）
> - ...
>
> ## 二、文件/类/函数/成员变量映射到 src 路径
> - ...
>
> ## 三、关键数据结构与对外接口（含类型/返回约束）
> - ...
>
> ## 四、与其他模块交互（调用链与责任边界）
> - ...
>
> ## 五、测试文件
> - 绑定文件与覆盖点...
> ```

示例参考：`docs/utils/SOP.md`、`docs/tool/SOP.md`、`docs/pipeline/SOP.md`。

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
6. **同步文档**：代码合入后同步更新相关 README、教程、示例等。

## 文档结构约定
- `docs/SOP.md`：全局总纲（本文件）。
- `docs/<模块名>/SOP.md`：模块级 SOP，需与目录结构保持一致，例如 `src/agentscope/_utils` → `docs/utils/SOP.md`；并严格遵循上文“模块 SOP 模板（强制）”的 5 节结构。
- 若模块内有更细分功能，可在子目录追加 `SOP_<功能>.md`，但必须在模块级 SOP 引用，保持索引一致。

### 验收规范（Documentation Acceptance）
- PR 描述需勾验：是否覆盖模板五节；若未变更代码仍需说明“不需更新”的理由。
- 功能定义章节必须包含并按序呈现“设计思路和逻辑/架构设计/核心组件逻辑/关键设计模式/其他组件的交互”；如适用，应附 1 张 Mermaid 或 ASCII 流程/结构图。
- 测试文件章节必须列出绑定测试与覆盖点；若存在空缺需给出补测计划。
- 评审口径：缺任一节、缺上述子结构、或缺关键字段（类型/返回/异常/并发行为）视为未达标，需补全后再评审。

## 模块索引（按 src/agentscope/ 映射）
- agent → docs/agent/SOP.md
  - subagent → docs/agent/subagent/SOP.md
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

## 关联模板与落地
- 建议优先按本模板重写 `docs/plan/SOP.md` 作为试点，并据此批量对齐其余模块。
- 如需补充跨模块调用链示例，应直接写入对应模块 SOP 的第四节（调用链与责任边界）。

## 关联文档
- `AGENTS.md`：行为准则与 workflow 原则。
- `docs/utils/SOP.md` 等子模块 SOP。
- `todo.md`：执行步骤与验收清单（由当前任务维护）。
- `specs/###-*/`：特性开发“变更河流”（spec/plan/tasks），不作为规范源。

> 备注：若子模块 SOP 出现明显偏差（未遵循本总纲、未及时更新、结构混乱），必须立即纠正并新增相应的验收项。
