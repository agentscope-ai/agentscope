# SOP：src/agentscope/evaluate 模块

## 一、功能定义（Scope/非目标）
### 1. 设计思路和逻辑
- 为 AgentScope 的应用提供统一的离线评测框架：定义任务（Task）、解决方案（Solution）、指标（Metric）、基准集合（Benchmark）及执行器（Evaluator），并可将结果持久化/聚合。
- 支持串行调试（GeneralEvaluator）与分布式/并行执行（RayEvaluator），复用同一任务与指标定义。
- 不直接评判业务逻辑，评测标准完全由用户编写的 `MetricBase` 决定；框架不管理评测数据分发或安全隔离。

### 2. 架构设计
```mermaid
graph TD
    subgraph Definition
        BM[BenchmarkBase]
        TK[Task]
        MT[MetricBase]
    end
    subgraph Runtime
        EVB[EvaluatorBase]
        GE[GeneralEvaluator]
        RE[RayEvaluator]
        STG[EvaluatorStorageBase]
        FST[FileEvaluatorStorage]
    end
    subgraph Result
        SOL[SolutionOutput]
        MET[MetricResult]
        AGG[aggregate()]
    end
    BM --> TK
    TK --> MT
    EVB --> GE
    EVB --> RE
    GE --> STG
    RE --> STG
    STG --> SOL
    STG --> MET
    EVB --> AGG
    SolutionRunner --> SOL
    SOL --> TK --> MT --> MET
```

### 3. 核心组件逻辑
- **Task**：描述单个评测样例，包含 `input`、`ground_truth`、`metrics`、`metadata`、`evaluate` 方法。`evaluate` 会遍历 `metrics` 调用并返回 `MetricResult` 列表。
- **MetricBase/MetricResult**：定义指标的名称、类型（类别/数值）、描述及可选类别列表；`__call__` 接收 `SolutionOutput` 并返回 `MetricResult`。
- **SolutionOutput**：记录被评系统产出的 `success` 状态、最终输出、工具轨迹 `trajectory`、额外 `meta`。
- **BenchmarkBase**：提供任务集合的迭代、索引与长度；可根据需要实现加载数据集、划分子集等逻辑。
- **EvaluatorBase**：管理评测主流程；保存评测元信息（`_save_evaluation_meta`）、运行评测（抽象 `run`）、聚合结果（`aggregate`）。
- **GeneralEvaluator**：调试友好的串行执行器；对每个 task/repeat 调用用户提供的 `solution` 协程，缓存 `SolutionOutput`，随后调用 `Task.evaluate` 并把结果写入存储。
- **RayEvaluator**：基于 Ray 的并行实现（当安装 Ray 时可用），接口与 GeneralEvaluator 保持一致。
- **EvaluatorStorageBase/FileEvaluatorStorage**：定义保存/读取 Solution、Metric、聚合结果的接口；文件实现将结果写入本地文件结构，并提供 `get_agent_pre_print_hook` 用于抓取 Agent 输出。
- **aggregate**：统计完成/未完成任务、各指标分布及数值聚合（均值/最大/最小），写入存储用于后续分析。

### 4. 关键设计模式
- **模板方法**：`EvaluatorBase` 规定评测流程（保存元信息 → 运行 → 聚合），具体执行策略由子类实现。
- **策略模式**：`MetricBase`、`EvaluatorStorageBase`、`BenchmarkBase` 作为可插拔策略，实现不同指标准则、存储后端或数据集。
- **数据传输对象**：`Task`、`SolutionOutput`、`MetricResult` 充当 DTO，便于序列化和存储。

### 5. 其他组件的交互
- **Agent/系统被测对象**：通过传入的 `solution(task, pre_print_hook)` 协程与评测框架交互，产生 `SolutionOutput`。
- **Toolkit/Agent Hooks**：`EvaluatorStorageBase.get_agent_pre_print_hook` 可在评测期间捕获 Agent 的 `print` 输出，形成轨迹。
- **文件/数据库**：`FileEvaluatorStorage` 使用本地文件夹保存结果；若需数据库或云存储，可自定义 `EvaluatorStorageBase` 实现。
- **责任边界**：评测框架不负责构建解决方案（需用户提供 `solution`）、不确保任务幂等性、也不处理数据分发（RayEvaluator 例外）。

## 二、文件/类/函数/成员变量映射到 src 路径
- `src/agentscope/evaluate/_task.py`
  - `Task`：任务定义及 `evaluate` 方法。
- `src/agentscope/evaluate/_solution.py`
  - `SolutionOutput`：记录解答结果、轨迹、元信息。
- `src/agentscope/evaluate/_metric_base.py`
  - `MetricBase`、`MetricResult`、`MetricType`。
- `src/agentscope/evaluate/_benchmark_base.py`
  - `BenchmarkBase`：任务集合抽象。
- `src/agentscope/evaluate/_evaluator/_evaluator_base.py`
  - `EvaluatorBase`：评测流程骨架、聚合逻辑。
- `src/agentscope/evaluate/_evaluator/_general_evaluator.py`
  - `GeneralEvaluator`：串行执行器。
- `src/agentscope/evaluate/_evaluator/_ray_evaluator.py`
  - `RayEvaluator`：基于 Ray 的并行执行器（取决于 `ray` 依赖）。
- `src/agentscope/evaluate/_evaluator_storage/_evaluator_storage_base.py`
  - `EvaluatorStorageBase`：解决方案与评测结果存储接口。
- `src/agentscope/evaluate/_evaluator_storage/_file_evaluator_storage.py`
  - `FileEvaluatorStorage`：将结果保存到文件系统，提供读取/存在检查。
- `src/agentscope/evaluate/_benchmark_base.py`、`_ace_benchmark`
  - 示例基准实现（ACE Benchmark）。
- `src/agentscope/evaluate/__init__.py`
  - 导出 Task、SolutionOutput、Evaluator、Metric、Benchmark 等。
- `src/agentscope/evaluate/CLAUDE.md`
  - 记录典型调用链。

## 三、关键数据结构与对外接口（含类型/返回约束）
- `Task`
  - 字段：`id: str`、`input`、`ground_truth`、`metrics: list[MetricBase]`、`tags`、`metadata`。
  - 方法：`async evaluate(solution: SolutionOutput) -> list[MetricResult]`。
- `SolutionOutput`
  - `success: bool`、`output: JSONSerializableObject`、`trajectory: list[ToolUseBlock | ToolResultBlock | TextBlock]`、`meta: dict[str, Any] | None`。
  - Pickle 友好的 `__getstate__/__setstate__`。
- `MetricBase.__call__(solution: SolutionOutput, **kwargs) -> MetricResult`
  - `MetricResult` 字段：`name`、`result`（数值或类别）、`created_at`、`message`、`metadata`。
- `EvaluatorBase.run(solution_callable)`（抽象）：由子类实现调度；参数为 `Callable[[Task, Callable], Coroutine[Any, Any, SolutionOutput]]`。
- `EvaluatorBase.aggregate()`：计算完成度、分布、聚合统计并调用存储保存。
- `EvaluatorStorageBase` 主要方法：
  - `save_solution_result` / `get_solution_result` / `solution_result_exists`
  - `save_evaluation_result` / `get_evaluation_result` / `evaluation_result_exists`
  - `save_evaluation_meta` / `save_aggregation_result` / `aggregation_result_exists`
  - `get_agent_pre_print_hook(task_id, repeat_id)` 返回用于捕获打印的 Hook。
- `FileEvaluatorStorage` 具体参数：`root_dir`、`auto_mkdir` 等（详见源码），以 JSON 或 Pickle 存储结果。

## 四、与其他模块交互（调用链与责任边界）
- **评测流程**：
  1. 构建 `Benchmark` (任务集合) 与 `Evaluator`。
  2. 提供 `solution(task, pre_print_hook)` 协程，内部运行 Agent 或模型。
  3. Evaluator 调用 Solution → 生成 `SolutionOutput` → 通过 `Task.evaluate` 计算指标 → `EvaluatorStorage` 写入。
  4. 评测结束后调用 `aggregate` 生成统计信息。
- **Plan/Memory/Toolkit**：在 solution 内部可结合多 Agent、Plan、Memory 等组件；评测框架对此保持透明。
- **并行执行**：RayEvaluator 使用 Ray 远程任务；需确保 `SolutionOutput`、`Task`、`Metric`、`Benchmark` 可序列化。
- **责任边界**：评测框架不保证解决方案幂等、不做任务调度重试；数据加载、缓存和权限控制由调用方负责。

## 五、测试文件
- 绑定文件：`tests/evaluation_test.py`
- 覆盖点：任务执行、指标打分、结果存储.
