# Plan 计划模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [源码解读](#3-源码解读)
   - [SubTask 子任务模型](#31-subtask-子任务模型)
   - [Plan 计划模型](#32-plan-计划模型)
   - [DefaultPlanToHint 状态感知提示生成](#33-defaultplantohint-状态感知提示生成)
   - [PlanNotebook 计划笔记本](#34-plannotebook-计划笔记本)
   - [存储层（PlanStorageBase / InMemoryPlanStorage）](#35-存储层)
4. [设计模式总结](#4-设计模式总结)
5. [代码示例](#5-代码示例)
6. [练习题](#6-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 SubTask 的四种状态和 PlanNotebook 的 8 个工具函数 | 列举、识别 |
| 理解 | 解释 DefaultPlanToHint 的状态机提示生成逻辑 | 解释、描述 |
| 应用 | 使用 PlanNotebook 的工具函数创建和管理计划 | 实现、操作 |
| 分析 | 分析 SubTask 状态转换的不变量约束 | 分析、追踪 |
| 评价 | 评价 plan_to_hint 可回调设计在不同场景下的适用性 | 评价、推荐 |
| 创造 | 设计一个自定义的 plan_to_hint 策略，支持并行子任务 | 设计、构建 |

## 先修检查

- [ ] Pydantic BaseModel 基础
- [ ] [StateModule](module_state_deep.md) 的 `register_state()` 机制
- [ ] Python async/await 和可调用对象（Callable）
- [ ] 有限状态机概念

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `SubTask` | JPA `@Entity` | Pydantic BaseModel ≈ JPA 实体 |
| `SubTask.state` | Spring StateMachine `State` | todo/in_progress/done/abandoned |
| `PlanNotebook` | `@Service` + `@Tool` | 业务逻辑 + 工具注册 |
| `DefaultPlanToHint` | 策略模式实现类 | 根据状态生成提示 |
| `PlanStorageBase` | `Repository` 接口 | 计划持久化抽象 |
| `register_state` | `@JsonSerialize` | 自定义序列化钩子 |

---

## 1. 模块概述

> **交叉引用**: Plan 模块为 ReActAgent 提供结构化的任务规划能力。PlanNotebook 继承自 StateModule，可通过 [Session 模块](module_session_deep.md) 持久化。ReActAgent 通过 [Tool 模块](module_tool_mcp_deep.md) 将 PlanNotebook 的工具函数注册给 LLM 使用，详见 [Agent 模块](module_agent_deep.md)。

Plan 模块实现了 AgentScope 的结构化规划系统，让 Agent 能够将复杂任务分解为有序子任务，逐步执行并追踪进度。这是 ReActAgent 实现复杂任务推理的关键支撑。

**核心能力**：

1. **任务分解**：将复杂目标分解为有序子任务列表
2. **状态追踪**：每个子任务有独立的状态机（todo → in_progress → done/abandoned）
3. **智能提示**：根据当前计划状态自动生成引导性提示
4. **历史管理**：支持计划完成/放弃后存入历史，可恢复历史计划

**源码位置**: `src/agentscope/plan/`（~1,100+ 行，4 个文件）

---

## 2. 目录结构

```
plan/
├── __init__.py                    # 导出接口（6 个公共类）
├── _plan_model.py                 # SubTask + Plan 数据模型（201 行）
├── _plan_notebook.py              # DefaultPlanToHint + PlanNotebook（905 行）
├── _storage_base.py               # PlanStorageBase 抽象
└── _in_memory_storage.py          # InMemoryPlanStorage 内存实现
```

---

## 3. 源码解读

### 3.1 SubTask 子任务模型

```python
class SubTask(BaseModel):
    name: str                    # 子任务名称（不超过 10 词）
    description: str             # 约束、目标和结果描述
    expected_outcome: str        # 具体、可衡量的预期结果
    outcome: str | None = None   # 实际结果
    state: Literal["todo", "in_progress", "done", "abandoned"] = "todo"
    created_at: str              # 创建时间戳（自动生成）
    finished_at: str | None = None  # 完成时间戳
```

**状态机**：

```
todo ──→ in_progress ──→ done
  │           │
  │           └──→ abandoned
  └──→ abandoned
```

**关键方法**：

| 方法 | 说明 |
|------|------|
| `finish(outcome: str)` | 标记为 done，记录实际结果和完成时间 |
| `to_oneline_markdown()` | 单行 Markdown 复选框表示 |
| `to_markdown(detailed=False)` | 详细 Markdown 表示 |

**Markdown 输出示例**：

```
# 简洁模式
- [] 数据收集              # todo
- [][WIP] 数据清洗          # in_progress
- [x] 需求分析             # done
- [][Abandoned] 旧方案      # abandoned

# 详细模式
### 数据清洗
  - 描述: 清洗原始数据，去除无效记录
  - 预期结果: 清洁数据集，无效记录 < 1%
  - 状态: in_progress
```

### 3.2 Plan 计划模型

```python
class Plan(BaseModel):
    id: str                       # 自动生成
    name: str                     # 计划名称
    description: str              # 计划描述
    expected_outcome: str         # 整体预期结果
    subtasks: list[SubTask]       # 子任务有序列表
    created_at: str               # 创建时间戳
    state: Literal["todo", "in_progress", "done", "abandoned"] = "todo"
    finished_at: str | None = None
    outcome: str | None = None
```

**关键方法**：

| 方法 | 说明 |
|------|------|
| `refresh_plan_state()` | 根据子任务状态自动更新计划状态 |
| `finish(state, outcome)` | 标记计划完成或放弃 |
| `to_markdown(detailed)` | 生成完整的计划 Markdown 文档 |

**`refresh_plan_state()` 逻辑**：

```python
def refresh_plan_state(self) -> str:
    # 如果有任何子任务是 in_progress → 计划也变为 in_progress
    # 如果所有子任务都是 todo → 计划保持 todo
    # 不会覆盖 done 或 abandoned 状态
```

### 3.3 DefaultPlanToHint 状态感知提示生成

`DefaultPlanToHint` 是一个可调用类，根据计划状态生成上下文相关的提示消息。

**五种状态提示模板**：

| 条件 | 模板变量 | 提示内容 |
|------|----------|----------|
| 无计划 | `no_plan` | 建议创建计划 |
| 所有子任务为 todo | `at_the_beginning` | 引导开始第一个子任务 |
| 有子任务进行中 | `when_a_subtask_in_progress` | 显示当前子任务详情 |
| 有完成但无进行中 | `when_no_subtask_in_progress` | 建议继续下一个子任务 |
| 全部完成/放弃 | `at_the_end` | 建议调用 finish_plan |

**提示包装格式**：

```xml
<system-hint>
[状态相关的提示内容]
</system-hint>
```

> **设计亮点**: 提示使用 XML 标签包装，确保 LLM 能区分系统提示和用户内容。这是 Prompt Engineering 中常用的结构化技术。

**决策流程**：

```
plan is None?
  ├── Yes → no_plan 提示
  └── No → 统计各状态子任务数量
        ├── 全部 todo → at_the_beginning
        ├── 有 in_progress → when_a_subtask_in_progress
        ├── 全部 done/abandoned → at_the_end
        └── 部分 done，无 in_progress → when_no_subtask_in_progress
```

### 3.4 PlanNotebook 计划笔记本

`PlanNotebook` 继承自 `StateModule`，是 Plan 模块的核心编排类。

```python
class PlanNotebook(StateModule):
    def __init__(self, max_subtasks=None, plan_to_hint=None, storage=None):
        super().__init__()
        self.max_tasks = max_subtasks
        self.plan_to_hint = plan_to_hint or DefaultPlanToHint()
        self.storage = storage or InMemoryPlanStorage()
        self.current_plan = None
        self._plan_change_hooks = OrderedDict()

        # 注册 current_plan 用于状态序列化
        self.register_state(
            "current_plan",
            custom_to_json=lambda p: p.model_dump() if p else None,
            custom_from_json=lambda d: Plan.model_validate(d) if d else None,
        )
```

**8 个工具函数**：

| 工具函数 | 参数 | 说明 |
|----------|------|------|
| `create_plan` | name, description, expected_outcome, subtasks | 创建新计划（替换已有的） |
| `revise_current_plan` | subtask_idx, action, subtask | 添加/修改/删除子任务 |
| `update_subtask_state` | subtask_idx, state | 更新子任务状态 |
| `finish_subtask` | subtask_idx, subtask_outcome | 完成子任务并自动激活下一个 |
| `view_subtasks` | subtask_idx | 查看指定子任务详情 |
| `finish_plan` | state, outcome | 完成/放弃当前计划 |
| `view_historical_plans` | (无) | 查看历史计划列表 |
| `recover_historical_plan` | plan_id | 恢复历史计划 |

**`list_tools()` 方法**返回这 8 个工具函数的列表，供 Toolkit 注册。

**状态不变量（重要！）**：

`update_subtask_state` 和 `finish_subtask` 强制执行以下不变量：

1. **单一进行中**：同一时刻只能有一个子任务处于 `in_progress`
2. **顺序执行**：必须按顺序完成子任务，不能跳过未完成的前置子任务

```python
# update_subtask_state 中的验证逻辑
for i in range(subtask_idx):
    if self.current_plan.subtasks[i].state not in ("done", "abandoned"):
        raise ValueError("前面的子任务必须先完成")
```

**Hook 系统**：

```python
# 注册计划变更钩子
notebook.register_plan_change_hook("ui_update", on_plan_change)
notebook.remove_plan_change_hook("ui_update")

# 钩子函数签名
async def on_plan_change(notebook: PlanNotebook, plan: Plan | None):
    # 当计划发生任何变更时触发
    pass
```

> **Java 对照**: 类似 Spring 的事件机制 `@EventListener(PlanChangeEvent.class)`。

**`finish_subtask` 的自动激活**：

```python
async def finish_subtask(self, subtask_idx, subtask_outcome):
    # 1. 标记当前子任务为 done
    self.current_plan.subtasks[subtask_idx].finish(subtask_outcome)
    # 2. 自动激活下一个子任务
    next_idx = subtask_idx + 1
    if next_idx < len(self.current_plan.subtasks):
        self.current_plan.subtasks[next_idx].state = "in_progress"
    # 3. 刷新计划状态
    self.current_plan.refresh_plan_state()
    # 4. 触发钩子
    await self._trigger_plan_change_hooks()
```

### 3.5 存储层

```python
class PlanStorageBase(ABC):
    @abstractmethod
    async def add_plan(self, plan: Plan) -> None: ...
    @abstractmethod
    async def delete_plan(self, plan_id: str) -> None: ...
    @abstractmethod
    async def get_plans(self) -> list[Plan]: ...
    @abstractmethod
    async def get_plan(self, plan_id: str) -> Plan | None: ...

class InMemoryPlanStorage(PlanStorageBase):
    # 使用 OrderedDict 存储，plan.id 为键
```

**存储时机**：计划在 `finish_plan()` 时才被存入历史。`current_plan` 不经过 StorageBase，而是通过 StateModule 的 `register_state` 序列化。

---

## 4. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **State Machine（状态机）** | SubTask.state, Plan.state | todo → in_progress → done/abandoned |
| **Strategy（策略）** | plan_to_hint 可回调 | 可替换的提示生成策略 |
| **Observer（观察者）** | _plan_change_hooks | 计划变更时通知注册的钩子 |
| **Template Method** | PlanNotebook 工具函数 | 定义变更-验证-触发钩子的统一流程 |
| **Memento** | state_dict + PlanStorageBase | 计划状态的保存与恢复 |

---

## 5. 代码示例

### 5.1 创建和执行计划

```python
from agentscope.plan import PlanNotebook, SubTask

notebook = PlanNotebook(max_subtasks=10)

# 创建计划
result = await notebook.create_plan(
    name="数据分析项目",
    description="完成客户行为数据分析",
    expected_outcome="完整的数据分析报告",
    subtasks=[
        SubTask(name="数据收集", description="从数据库提取原始数据",
                expected_outcome="原始数据 CSV 文件"),
        SubTask(name="数据清洗", description="去除无效和重复记录",
                expected_outcome="清洁数据集"),
        SubTask(name="分析建模", description="构建客户行为模型",
                expected_outcome="模型和可视化结果"),
    ],
)
print(result.content)
```

**运行输出**：
```
计划「数据分析项目」已创建，包含 3 个子任务：
- [] 数据收集
- [] 数据清洗
- [] 分析建模
```

### 5.2 逐步执行子任务

```python
# 开始第一个子任务
await notebook.update_subtask_state(0, "in_progress")

# 完成并自动激活下一个
await notebook.finish_subtask(0, subtask_outcome="收集到 10,000 条客户记录")

# 查看当前状态
hint = await notebook.get_current_hint()
print(hint.content)
```

**运行输出**：
```
<system-hint>
当前正在进行子任务 2「数据清洗」...
</system-hint>
```

### 5.3 自定义提示策略

```python
class ParallelPlanToHint:
    """支持并行子任务的提示策略"""

    def __call__(self, plan):
        if plan is None:
            return "请先创建执行计划"

        in_progress = [s for s in plan.subtasks if s.state == "in_progress"]
        if in_progress:
            names = ", ".join(s.name for s in in_progress)
            return f"正在并行执行: {names}"

        return None

notebook = PlanNotebook(plan_to_hint=ParallelPlanToHint())
```

---

## 6. 练习题

### 基础题

**Q1**: `finish_subtask` 为什么会自动激活下一个子任务？如果不自动激活，Agent 需要做什么？

**Q2**: `DefaultPlanToHint` 为什么将提示包装在 `<system-hint>` 标签中？

### 中级题

**Q3**: 分析 `update_subtask_state` 的不变量检查。如果移除"顺序执行"约束，可能产生什么问题？

**Q4**: `PlanNotebook.register_state("current_plan", ...)` 使用了自定义序列化钩子。为什么不能直接依赖 `__setattr__` 自动追踪？

### 挑战题

**Q5**: 设计一个支持**并行子任务**的 `ParallelGroupPlan`，允许多个子任务同时处于 `in_progress` 状态。需要修改哪些不变量检查？提示策略应如何调整？

---

### 参考答案

**A1**: 自动激活简化了 Agent 的工作流——它只需调用 `finish_subtask` 即可，无需额外调用 `update_subtask_state` 来开始下一个任务。如果不自动激活，Agent 需要在每次完成子任务后手动调用 `update_subtask_state(next_idx, "in_progress")`，增加了出错概率。

**A2**: XML 标签包装是一种 Prompt Engineering 技术，帮助 LLM 区分系统提示和实际用户对话内容。LLM 在处理多轮对话时，能明确识别 `<system-hint>` 内的内容是指令性提示而非对话内容，从而更准确地遵循引导。

**A3**: 移除顺序约束后，Agent 可能跳过关键的前置步骤。例如，在"数据收集 → 数据清洗 → 分析建模"流程中，如果允许跳到"分析建模"，Agent 会在没有数据的情况下尝试建模。顺序约束确保了依赖关系的正确性。

**A4**: `current_plan` 是 `Plan` 类型（Pydantic BaseModel），不继承自 `StateModule`。`__setattr__` 的自动追踪只对 `StateModule` 子类生效。因此需要通过 `register_state` 注册，并提供 `custom_to_json`（使用 `model_dump()`）和 `custom_from_json`（使用 `Plan.model_validate()`）钩子来实现序列化。

**A5**: 关键修改点：(1) `update_subtask_state` 移除"单一进行中"约束，允许多个子任务同时为 `in_progress`；(2) `finish_subtask` 不自动激活下一个，因为并行任务的完成顺序不确定；(3) 提示策略需要显示所有进行中的子任务及其进度；(4) 需要新增"依赖关系"字段，标记哪些子任务可以并行、哪些有前置依赖。

---

## 模块小结

| 概念 | 要点 |
|------|------|
| SubTask | Pydantic 模型，四状态有限状态机 |
| Plan | 子任务有序列表，自动同步状态 |
| DefaultPlanToHint | 根据计划状态生成 5 种引导提示 |
| PlanNotebook | StateModule 子类，8 个工具函数，Hook 系统 |
| 状态不变量 | 单一进行中 + 顺序执行 |
| register_state | Plan 的 Pydantic 序列化需要自定义钩子 |

## 章节关联

| 相关模块 | 关联点 |
|----------|--------|
| [State 模块](module_state_deep.md) | PlanNotebook 继承 StateModule |
| [Agent 模块](module_agent_deep.md) | ReActAgent 使用 PlanNotebook |
| [Session 模块](module_session_deep.md) | Session 持久化 PlanNotebook 状态 |
| [Tool 模块](module_tool_mcp_deep.md) | Toolkit 注册 PlanNotebook 的工具函数 |
| [Tracing 模块](module_tracing_deep.md) | 追踪计划变更操作 |

**版本参考**: AgentScope >= 1.0.0 | 源码 `plan/`
