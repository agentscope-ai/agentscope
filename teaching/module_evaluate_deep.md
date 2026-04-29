# Evaluate 评估模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [源码解读](#3-源码解读)
   - [BenchmarkBase 基准抽象](#31-benchmarkbase-基准抽象)
   - [Task 评估任务](#32-task-评估任务)
   - [MetricBase 指标基类](#33-metricbase-指标基类)
   - [EvaluatorBase 评估器](#34-evaluatorbase-评估器)
   - [ACEBenchmark 评估基准](#35-acebenchmark-评估基准)
4. [设计模式总结](#4-设计模式总结)
5. [代码示例](#5-代码示例)
6. [练习题](#6-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 Evaluate 模块的核心类和评估流程 | 列举、识别 |
| 理解 | 解释 Benchmark-Task-Metric 三层评估架构 | 解释、描述 |
| 应用 | 使用 ACEBenchmark 对 Agent 进行能力评估 | 实现、操作 |
| 分析 | 分析 EvaluatorBase 的聚合统计流程 | 分析、追踪 |
| 评价 | 评价不同指标类型（CATEGORY vs NUMERICAL）的适用场景 | 评价、推荐 |
| 创造 | 设计一个自定义 Benchmark 和 Metric 用于特定领域评估 | 设计、构建 |

## 先修检查

- [ ] Python dataclass 和 Pydantic BaseModel
- [ ] Python 异步编程基础
- [ ] 了解 Agent 评估的基本概念

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `BenchmarkBase` | JUnit 5 `TestDescriptor` | 评估任务集合的抽象 |
| `Task` | `@ParameterizedTest` + 测试数据 | 单个评估任务 |
| `MetricBase` | Hamcrest `Matcher` | 结果评判标准 |
| `EvaluatorBase` | JUnit 5 `Launcher` | 评估执行和聚合 |
| `SolutionOutput` | 测试结果对象 | 被评估的解决方案输出 |
| `FileEvaluatorStorage` | Surefire 报告插件 | 评估结果持久化 |

---

## 1. 模块概述

> **交叉引用**: Evaluate 模块提供 Agent 能力的系统化评估框架。它与 Tuner 模块配合使用——评估结果可指导 Agent 的强化学习调优，详见 [Tuner 模块](module_tuner_deep.md)。ACEBenchmark 使用模拟手机环境评估 Agent 的多步和多轮交互能力。

Evaluate 模块实现了 AgentScope 的 Agent 评估框架，提供从基准定义、任务管理、指标计算到结果聚合的完整评估流水线。

**核心能力**：

1. **基准抽象**：可迭代、可索引的任务集合
2. **指标系统**：支持分类指标和数值指标
3. **评估编排**：多轮重复评估、统计聚合
4. **内置基准**：ACE（Agent Capability Evaluation）基准

**源码位置**: `src/agentscope/evaluate/`（~277+ 行核心文件）

---

## 2. 目录结构

```
evaluate/
├── __init__.py                        # 导出接口（15 个符号）
├── _benchmark_base.py                 # BenchmarkBase 抽象（44 行）
├── _task.py                           # Task 数据类（54 行）
├── _metric_base.py                    # MetricBase + MetricResult + MetricType
├── _solution.py                       # SolutionOutput 类型定义
├── _evaluator/                        # 评估器子包
│   ├── _evaluator_base.py             # EvaluatorBase（305 行）
│   ├── _general_evaluator.py          # GeneralEvaluator 单进程评估器
│   └── _ray_evaluator.py              # RayEvaluator 分布式评估器
├── _evaluator_storage/                # 存储后端子包
│   ├── _evaluator_storage_base.py     # EvaluatorStorageBase 抽象
│   └── _file_evaluator_storage.py     # FileEvaluatorStorage 实现
├── _ace_benchmark/                    # ACE 评估基准
│   ├── _ace_benchmark.py              # ACEBenchmark（241 行）
│   ├── _ace_tools_zh.py               # 中文工具集
│   ├── _ace_metric.py                 # ACEAccuracy + ACEProcessAccuracy
│   └── _ace_tools_api/                # API 模拟子包
```

---

## 3. 源码解读

### 3.1 BenchmarkBase 基准抽象

```python
class BenchmarkBase(ABC):
    name: str
    description: str

    @abstractmethod
    def __iter__(self) -> Generator[Task, None, None]: ...

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def __getitem__(self, index: int) -> Task: ...
```

**设计简洁**：实现了 Python 的迭代协议，使得 `BenchmarkBase` 实例可以像列表一样被遍历、索引和获取长度。

```python
benchmark = ACEBenchmark(data_dir="./data")
for task in benchmark:       # __iter__
    print(task.id)
print(len(benchmark))        # __len__
task = benchmark[0]          # __getitem__
```

### 3.2 Task 评估任务

```python
@dataclass
class Task:
    id: str                                # 唯一标识
    input: JSONSerializableObject          # 任务输入
    ground_truth: JSONSerializableObject   # 预期答案
    metrics: list[MetricBase]              # 评估指标列表
    tags: dict[str, str] | None = None     # 分类标签
    metadata: dict[str, Any] | None = None # 额外元数据

    async def evaluate(self, solution: SolutionOutput) -> list[MetricResult]:
        """使用所有指标评估解决方案"""
        results = []
        for metric in self.metrics:
            result = await metric(solution)
            results.append(result)
        return results
```

**`tags` 的用途**：按维度分类任务，如 `{"difficulty": "easy", "category": "math"}`，用于聚合分析时按维度统计。

### 3.3 MetricBase 指标基类

#### MetricType 枚举

```python
class MetricType(str, Enum):
    CATEGORY = "category"    # 分类指标（如正确/错误）
    NUMERICAL = "numerical"  # 数值指标（如分数、BLEU 值）
```

#### MetricBase 数据类

```python
@dataclass
class MetricBase(ABC):
    name: str                          # 指标名称
    metric_type: MetricType            # 指标类型
    description: str | None = None     # 指标描述
    categories: list[str] | None = None # CATEGORY 类型的分类列表

    def __init__(self, name, metric_type, description=None, categories=None):
        self.name = name
        self.metric_type = metric_type
        self.description = description
        # CATEGORY 类型必须提供分类列表
        if metric_type == MetricType.CATEGORY and categories is None:
            raise ValueError("Categories must be provided for category metrics.")
        self.categories = categories

    @abstractmethod
    async def __call__(self, *args, **kwargs) -> MetricResult: ...
```

**重要约束**：`CATEGORY` 类型的指标必须在构造时提供 `categories` 列表（如 `["correct", "incorrect"]`），否则抛出 `ValueError`。

#### MetricResult 数据类

```python
@dataclass
class MetricResult(DictMixin):
    name: str                                        # 指标名称
    result: str | float | int                        # 评估结果值
    created_at: str = field(default_factory=...)     # 创建时间戳
    message: str | None = None                       # 附加消息
    metadata: dict[str, ...] | None = None           # 额外元数据
```

**两种指标类型的聚合方式不同**：

| 指标类型 | 示例 | 聚合方式 |
|----------|------|----------|
| CATEGORY | 正确/错误/部分正确 | 计数分布（正确: 80, 错误: 20） |
| NUMERICAL | BLEU 分数、相似度 | 均值、最大值、最小值 |

### 3.4 EvaluatorBase 评估器

```python
class EvaluatorBase:
    def __init__(self, name: str, benchmark: BenchmarkBase,
                 n_repeat: int, storage: EvaluatorStorageBase):
        ...

    @abstractmethod
    async def run(self, solution: Callable) -> None: ...

    async def aggregate(self) -> None:
        """聚合所有评估结果"""
        # 遍历 tasks × repeats，统计：
        # - LLM 调用次数
        # - Agent 调用次数
        # - 工具调用次数
        # - 嵌入调用次数
        # - Token 用量（input/output）
        # - 每个指标的 CATEGORY/NUMERICAL 聚合
```

**评估流水线**：

```
1. _save_evaluation_meta()    → 保存评估元数据
2. for task in benchmark:     → 遍历每个任务
3.   for repeat in range(n):  → 每个任务重复 n 次
4.     solution(task)         → 执行解决方案
5.     task.evaluate(result)  → 评估结果
6.     _save_task_meta(task)  → 保存任务元数据
7. aggregate()                → 聚合统计
```

**聚合维度**：

```
meta_info
├── name, timestamp, total_repeats
├── benchmark_info
└── repeats
    └── {repeat_id}
        ├── completed_tasks: [...]
        ├── incomplete_tasks: [...]
        ├── llm_calls: {total, avg}
        ├── agent_calls: {total, avg}
        ├── tool_calls: {total, avg}
        ├── embedding_calls: {total, avg}
        ├── chat_usage: {input_tokens, output_tokens}
        └── metrics
            └── {metric_name}
                ├── type: "category" | "numerical"
                ├── [category]: {count, percentage}  # CATEGORY 类型
                └── [numerical]: {mean, max, min}     # NUMERICAL 类型
```

### 3.5 ACEBenchmark 评估基准

```python
class ACEBenchmark(BenchmarkBase):
    data_dir_url = "https://raw.githubusercontent.com/ACEBench/ACEBench/main/data_all"
    data_subdir = ["data_zh"]
    ground_truth_dir = "possible_answer"
```

**ACE 评估流程**：

```
1. 下载数据（如果本地不存在）
2. 加载 JSON5 格式的任务数据 + ground truth
3. 为每个数据项创建 Task：
   ├── input: 任务描述
   ├── ground_truth: 预期结果 + milestone
   ├── metrics: [ACEAccuracy, ACEProcessAccuracy]
   └── metadata: {phone: ACEPhone, tools: [...]}
```

**ACEPhone 模拟环境**：

ACE 使用模拟手机环境评估 Agent 的多步操作能力。Agent 需要通过工具调用来操作虚拟手机（如拨打电话、发送短信、设置闹钟），评估其任务完成度。

**两种评估指标**：

| 指标 | 说明 |
|------|------|
| `ACEAccuracy` | 结果准确度——最终状态是否匹配 ground truth |
| `ACEProcessAccuracy` | 过程准确度——中间步骤是否匹配 milestone |

---

## 4. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Iterator（迭代器）** | BenchmarkBase | 统一的任务遍历接口 |
| **Strategy（策略）** | MetricBase | 可替换的评估指标 |
| **Template Method** | EvaluatorBase.run/aggregate | 标准化的评估流水线 |
| **Composite** | Task 包含多个 Metric | 多维度评估组合 |

---

## 5. 代码示例

### 5.1 使用 ACEBenchmark 评估 Agent

```python
from agentscope.evaluate import ACEBenchmark, GeneralEvaluator, SolutionOutput, FileEvaluatorStorage

# 创建基准和评估器
benchmark = ACEBenchmark(data_dir="./data/ace")
storage = FileEvaluatorStorage(save_dir="./eval_results")
evaluator = GeneralEvaluator(
    name="agent_eval_v1",
    benchmark=benchmark,
    n_repeat=3,        # 每个任务重复 3 次
    storage=storage,
    n_workers=4,       # 并行工作线程数
)

# 定义解决方案函数
async def my_solution(task, pre_hook):
    # 将 task.input 交给 Agent 处理
    result = await agent.reply(task.input)
    return SolutionOutput(
        success=True,
        output=result.content,
        trajectory=[],  # 工具调用轨迹
    )

# 运行评估
await evaluator.run(solution=my_solution)

# 聚合结果
await evaluator.aggregate()
```

### 5.2 自定义评估指标

```python
from agentscope.evaluate import MetricBase, MetricResult, MetricType

class ExactMatchMetric(MetricBase):
    """精确匹配指标"""

    def __init__(self, expected: str):
        super().__init__(
            name="exact_match",
            metric_type=MetricType.CATEGORY,
            categories=["correct", "incorrect"],
        )
        self.expected = expected

    async def __call__(self, solution: SolutionOutput) -> MetricResult:
        is_correct = str(solution.output).strip() == self.expected.strip()
        return MetricResult(
            name="exact_match",
            result="correct" if is_correct else "incorrect",
        )

# 使用示例
metric = ExactMatchMetric(expected="42")
result = await metric(SolutionOutput(success=True, output="42", trajectory=[]))
print(result.result)  # "correct"
```

---

## 6. 练习题

### 基础题

**Q1**: `BenchmarkBase` 为什么同时实现 `__iter__`、`__len__` 和 `__getitem__`？只实现 `__iter__` 不够吗？

**Q2**: `MetricType.CATEGORY` 和 `MetricType.NUMERICAL` 的聚合方式有什么不同？

### 中级题

**Q3**: `Task.evaluate()` 中的指标是异步执行的（`await metric(solution)`）。为什么指标计算需要异步？

**Q4**: `EvaluatorBase.aggregate()` 跟踪了五个维度的统计（llm/agent/tool/embedding/chat_usage）。这些统计数据对评估 Agent 有什么意义？

### 挑战题

**Q5**: 设计一个 `HumanEvalBenchmark`，基于 HumanEval 编程基准评估 Agent 的代码生成能力。需要考虑哪些指标和聚合策略？

---

### 参考答案

**A1**: `__iter__` 支持遍历，`__len__` 支持获取总数（用于进度显示和聚合），`__getitem__` 支持随机访问特定任务（用于并行评估时的任务分配）。只有 `__iter__` 的话，无法高效地获取总数或按索引访问。

**A2**: CATEGORY 指标按值计数并计算百分比（如正确: 80%, 错误: 20%），适合离散分类结果。NUMERICAL 指标计算统计值（均值、最大值、最小值），适合连续数值结果（如 BLEU 分数、相似度）。

**A3**: 指标计算可能涉及异步操作，例如调用 LLM 作为评判（LLM-as-a-Judge 模式），或者查询外部评分 API。异步设计确保评估过程不会阻塞事件循环。

**A4**: 这五个维度提供了 Agent 执行效率的全面画像：LLM 调用次数反映推理开销，Agent 调用次数反映多 Agent 协作复杂度，工具调用次数反映工具使用频率，嵌入调用次数反映检索开销，Token 用量直接关联成本。这些数据帮助优化 Agent 的效率和经济性。

**A5**: 关键设计：(1) 数据：加载 HumanEval 的 164 个编程问题；(2) 指标：`Pass@k`（k=1,10,100）作为 NUMERICAL 指标，需要多次采样计算；(3) 执行环境：沙箱中运行生成的代码，捕获异常和输出；(4) 聚合：按难度级别分组统计 Pass@k 值。

---

## 模块小结

| 概念 | 要点 |
|------|------|
| BenchmarkBase | 可迭代、可索引的任务集合抽象 |
| Task | 单个评估任务，包含输入、ground truth、指标列表 |
| MetricBase | 可替换的评估指标，支持 CATEGORY 和 NUMERICAL |
| EvaluatorBase | 评估编排器，多轮重复 + 五维统计聚合 |
| ACEBenchmark | 内置的 Agent 能力评估基准 |

## 章节关联

| 相关模块 | 关联点 |
|----------|--------|
| [Tuner 模块](module_tuner_deep.md) | 评估结果指导 Agent 调优 |
| [Agent 模块](module_agent_deep.md) | Agent 作为被评估的解决方案 |
| [Tool 模块](module_tool_mcp_deep.md) | ACE 中 Agent 需要调用工具完成任务 |
| [Tracing 模块](module_tracing_deep.md) | 评估中的调用统计可结合 Tracing 数据 |

**版本参考**: AgentScope >= 1.0.0 | 源码 `evaluate/`
