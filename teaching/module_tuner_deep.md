# Tuner 调优模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [源码解读](#3-源码解读)
   - [tune() 入口函数](#31-tune-入口函数)
   - [WorkflowType 工作流定义](#32-workflowtype-工作流定义)
   - [JudgeType 评判函数](#33-judgetype-评判函数)
   - [AlgorithmConfig 算法配置](#34-algorithmconfig-算法配置)
   - [DatasetConfig 数据集配置](#35-datasetconfig-数据集配置)
   - [TunerModelConfig 模型配置](#36-tunermodelconfig-模型配置)
   - [PromptTune 提示词优化](#37-prompttune-提示词优化)
4. [设计模式总结](#4-设计模式总结)
5. [代码示例](#5-代码示例)
6. [练习题](#6-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 Tuner 模块的核心配置类和 tune() 参数 | 列举、识别 |
| 理解 | 解释 Workflow-Judge 双输出设计的工作原理 | 解释、描述 |
| 应用 | 使用 tune() 函数配置和启动 Agent 调优 | 实现、配置 |
| 分析 | 分析 GRPO 算法与 SFT 算法的调优策略差异 | 分析、对比 |
| 评价 | 评价 Tuner 与 Trinity-RFT 的集成方式 | 评价、推荐 |
| 创造 | 设计一个完整的工作流+评判函数用于特定业务场景的 Agent 调优 | 设计、构建 |

## 先修检查

- [ ] 强化学习基础概念（奖励信号、策略梯度）
- [ ] 监督微调（SFT）基础
- [ ] HuggingFace Datasets 基础
- [ ] Pydantic BaseModel 配置管理
- [ ] Python Callable 和 Awaitable 类型

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `tune()` | `Trainer.train()` (PyTorch) | 训练入口 |
| `WorkflowType` | `Trainable` 接口 | 被训练的工作流 |
| `JudgeType` | `LossFunction` | 奖励/损失计算 |
| `AlgorithmConfig` | `TrainingArguments` (HF) | 超参数配置 |
| `DatasetConfig` | `DatasetBuilder` | 数据加载配置 |
| `TunerModelConfig` | 模型配置类 | 被调优模型的参数 |
| `TinkerConfig` | LoRA 配置 | 低秩适应微调 |

---

## 1. 模块概述

> **交叉引用**: Tuner 模块提供 Agent 的强化学习调优能力。它与 [Evaluate 模块](module_evaluate_deep.md) 互补——评估发现问题，调优解决问题。调优后的模型通过 [Model 模块](module_model_deep.md) 提供推理服务。Trinity-RFT 是底层的 RL 训练框架。

Tuner 模块是 AgentScope 的 Agent 学习/调优框架，基于 Trinity-RFT 实现。它提供了从数据配置、工作流定义、奖励计算到模型训练的完整 RL/SFT 调优流水线。

**核心能力**：

1. **工作流定义**：用户定义 Agent 执行流程（WorkflowType）
2. **奖励信号**：通过 Judge 函数或直接奖励提供 RL 信号
3. **算法支持**：GRPO（多步策略优化）、SFT（监督微调）
4. **LoRA 微调**：通过 Tinker 支持低秩适应
5. **提示词优化**：独立的 prompt_tune 子模块

**源码位置**: `src/agentscope/tuner/`（~737 行核心文件 + 子模块）

---

## 2. 目录结构

```
tuner/
├── __init__.py                    # 导出接口（14 个符号）
├── _tune.py                       # tune() 入口函数（97 行）
├── _workflow.py                   # WorkflowType + WorkflowOutput（55 行）
├── _judge.py                      # JudgeType + JudgeOutput（40 行）
├── _algorithm.py                  # AlgorithmConfig（43 行）
├── _dataset.py                    # DatasetConfig（62 行）
├── _model.py                      # TunerModelConfig + TinkerConfig（149 行）
├── _config.py                     # 校验函数
├── prompt_tune/                   # 提示词优化子模块
│   └── ...                        # tune_prompt(), PromptTuneConfig
└── model_selection/               # 模型选择子模块
    └── ...                        # select_model()
```

---

## 3. 源码解读

### 3.1 tune() 入口函数

```python
def tune(
    *,
    workflow_func: WorkflowType,           # 必需：工作流函数
    judge_func: JudgeType | None = None,   # 可选：评判函数
    train_dataset: DatasetConfig | None = None,  # 训练数据
    eval_dataset: DatasetConfig | None = None,   # 评估数据
    model: TunerModelConfig | None = None,        # 模型配置
    auxiliary_models: dict[str, TunerModelConfig] | None = None,  # 辅助模型
    algorithm: AlgorithmConfig | None = None,     # 算法配置
    project_name: str | None = None,      # 项目名
    experiment_name: str | None = None,   # 实验名
    monitor_type: str | None = None,      # 监控类型
    config_path: str | None = None,       # 配置文件路径
) -> None:
```

**所有参数均为关键字参数**（`*` 强制），提高可读性。

**执行流程**：

```
tune()
  ↓
1. 校验 workflow_func（必需）和 judge_func（可选）
  ↓
2. 构建 Trinity-RFT 配置
  ↓
3. 检测 Aliyun PAI DLC 环境（可选）
  ↓
4. 调用 Trinity run_stage() 启动训练
```

**Trinity-RFT 集成**：

```python
# 惰性导入 Trinity
from trinity.cli.launcher import run_stage

# 如果未安装
# ImportError: Please install trinity-rft
```

> **设计哲学**: AgentScope 的 Tuner 不实现训练算法本身，而是将配置委托给 Trinity-RFT。这保持了框架的模块化和关注点分离。

### 3.2 WorkflowType 工作流定义

```python
class WorkflowOutput(BaseModel):
    reward: float | None = None        # 直接奖励（无需 Judge）
    response: Any | None = None        # 原始响应（交给 Judge 评估）
    metrics: Dict[str, float] | None = None  # 额外指标

WorkflowType = Callable[..., Awaitable[WorkflowOutput]]
```

**双输出设计**：

```
                    WorkflowOutput
                   /              \
          reward ≠ None        response ≠ None
              │                      │
       直接奖励模式            Judge 评估模式
       (无需 Judge)           (需要 judge_func)
              │                      │
         直接用于 RL         Judge 计算 reward
                              → JudgeOutput
```

**工作流函数签名**：

```python
async def my_workflow(
    task: Dict,                                    # 任务数据
    model: ChatModelBase,                          # 被调优的模型
    system_prompt: str,                            # 系统提示词
    auxiliary_models: Dict[str, ChatModelBase] | None = None,  # 辅助模型
    logger: Logger | None = None,                  # 日志记录器
) -> WorkflowOutput:
    ...
```

### 3.3 JudgeType 评判函数

```python
class JudgeOutput(BaseModel):
    reward: float                    # 标量奖励值（必需）
    metrics: Dict[str, float] | None = None  # 额外指标

JudgeType = Callable[..., Awaitable[JudgeOutput]]
```

**评判函数签名**：

```python
async def my_judge(
    task: Dict,                                    # 原始任务
    response: Any,                                 # 工作流的响应
    auxiliary_models: Dict[str, ChatModelBase] | None = None,  # 辅助模型（LLM-as-Judge）
    logger: Logger | None = None,
) -> JudgeOutput:
    ...
```

**LLM-as-Judge 模式**：

```
工作流执行 → response
              ↓
Judge 使用 auxiliary_models 调用 LLM 评估 response 质量
              ↓
返回 JudgeOutput(reward=0.85, metrics={"coherence": 0.9, "accuracy": 0.8})
```

### 3.4 AlgorithmConfig 算法配置

```python
class AlgorithmConfig(BaseModel):
    algorithm_type: str = "multi_step_grpo"  # 算法类型
    learning_rate: float = 1e-6              # 学习率
    group_size: int = 8                      # GRPO 组大小
    batch_size: int = 32                     # 批大小
    save_interval_steps: int = 100           # 保存间隔
    eval_interval_steps: int = 100           # 评估间隔
```

**GRPO vs SFT**：

| 维度 | GRPO | SFT |
|------|------|-----|
| 算法类型 | `multi_step_grpo` | `sft` |
| 训练信号 | 奖励信号（Judge/直接奖励） | 标签数据（ground truth） |
| 需要 Judge | 是（或直接奖励） | 否 |
| 适用场景 | 优化 Agent 行为策略 | 学习特定任务模式 |
| group_size | 用于组内比较排序 | 不适用 |

> **GRPO（Group Relative Policy Optimization）**：AgentScope 推荐的多步策略优化算法，适用于大多数 Agent 调优场景。

### 3.5 DatasetConfig 数据集配置

```python
class DatasetConfig(BaseModel):
    path: str                    # HuggingFace 数据集路径或本地路径
    name: str | None = None      # 数据集配置名
    split: str | None = "train"  # 数据分割
    total_epochs: int = 1        # 总训练轮数
    total_steps: int | None = None  # 总步数（覆盖 total_epochs）
```

**`preview()` 方法**：

```python
def preview(self, n: int = 5) -> List:
    dataset = load_dataset(
        path=self.path, name=self.name,
        split=self.split, streaming=True,
    )
    samples = list(dataset.take(n))
    for sample in samples:
        print(json.dumps(sample, indent=2, ensure_ascii=False))
    return samples
```

> **设计亮点**: 使用 `streaming=True` 避免下载完整数据集，适合快速预览和验证数据格式。

### 3.6 TunerModelConfig 模型配置

```python
class TunerModelConfig(BaseModel):
    model_path: str                     # 模型路径
    max_model_len: int                  # 最大上下文+生成长度
    temperature: float = 1.0            # 采样温度
    top_p: float = 1.0                  # Top-p 采样
    max_tokens: int = 8192              # 最大生成 Token
    enable_thinking: bool | None = None # 思考模式（Qwen3 特有）
    tensor_parallel_size: int = 1       # 张量并行度
    inference_engine_num: int = 1       # 推理引擎数
    tool_call_parser: str = "hermes"    # 工具调用解析器
    reasoning_parser: str = "deepseek_r1"  # 推理解析器
    tinker_config: TinkerConfig | None = None  # LoRA 配置
```

**TinkerConfig（LoRA 微调）**：

```python
class TinkerConfig(BaseModel):
    rank: int = 16               # LoRA 秩
    seed: int | None = None      # 初始化种子
    train_mlp: bool = True       # 训练 MLP 层
    train_attn: bool = True      # 训练注意力层
    train_unembed: bool = True   # 训练反嵌入层
    base_url: str | None = None  # Tinker 服务 URL
```

> **Java 对照**: LoRA 类似于只更新部分权重的模型微调，类似于 Java 中通过动态代理只拦截部分方法调用。

### 3.7 PromptTune 提示词优化

`prompt_tune/` 子模块提供了不修改模型权重的提示词优化能力：

```python
from agentscope.tuner import tune_prompt, PromptTuneConfig
```

与 `tune()` 不同，PromptTune 优化的是系统提示词而非模型参数，适合在模型微调不可行时的快速优化。

---

## 4. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Facade（外观）** | `tune()` 函数 | 统一的训练入口，隐藏 Trinity-RFT 复杂性 |
| **Strategy（策略）** | WorkflowType / JudgeType | 用户自定义工作流和评判策略 |
| **Builder** | 各 Config 类 | Pydantic BaseModel 作为配置构建器 |
| **Template Method** | 训练流水线 | 配置 → 校验 → 训练 → 评估的固定流程 |
| **Plugin** | Trinity-RFT 集成 | 外部训练框架作为可插拔后端 |

---

## 5. 代码示例

### 5.1 定义工作流和评判函数

```python
from agentscope.tuner import WorkflowOutput, JudgeOutput

async def customer_service_workflow(
    task, model, system_prompt, auxiliary_models=None, logger=None
) -> WorkflowOutput:
    # 使用被调优的模型处理客户问题
    response = await model(
        messages=[{"role": "user", "content": task["question"]}],
        system=system_prompt,
    )
    return WorkflowOutput(response=response.content)

async def quality_judge(
    task, response, auxiliary_models=None, logger=None
) -> JudgeOutput:
    # 使用辅助模型评估回答质量
    judge_model = auxiliary_models.get("judge")
    evaluation = await judge_model(
        messages=[{
            "role": "user",
            "content": f"评估以下回答的质量（0-1分）：\n问题: {task['question']}\n回答: {response}"
        }]
    )
    score = float(evaluation.content)
    return JudgeOutput(reward=score)
```

### 5.2 配置和启动调优

```python
from agentscope.tuner import (
    tune, AlgorithmConfig, DatasetConfig, TunerModelConfig
)

tune(
    workflow_func=customer_service_workflow,
    judge_func=quality_judge,
    train_dataset=DatasetConfig(
        path="my_org/customer_service_data",
        split="train",
        total_epochs=3,
    ),
    eval_dataset=DatasetConfig(
        path="my_org/customer_service_data",
        split="test",
    ),
    model=TunerModelConfig(
        model_path="Qwen/Qwen3-8B",
        max_model_len=8192,
        max_tokens=4096,
    ),
    algorithm=AlgorithmConfig(
        algorithm_type="multi_step_grpo",
        learning_rate=1e-6,
        group_size=8,
        batch_size=32,
    ),
    project_name="customer_service_agent",
    experiment_name="grpo_v1",
)
```

### 5.3 使用 LoRA 微调

```python
from agentscope.tuner import TunerModelConfig, TinkerConfig

model_config = TunerModelConfig(
    model_path="Qwen/Qwen3-8B",
    max_model_len=8192,
    tinker_config=TinkerConfig(
        rank=16,
        train_mlp=True,
        train_attn=True,
        train_unembed=False,  # 不训练反嵌入层
    ),
)
```

---

## 6. 练习题

### 基础题

**Q1**: `WorkflowOutput` 的 `reward` 和 `response` 字段为什么是互斥的？什么时候用直接奖励，什么时候用 Judge？

**Q2**: `AlgorithmConfig` 默认使用 `multi_step_grpo` 算法。GRPO 中的 `group_size` 参数有什么作用？

### 中级题

**Q3**: `tune()` 函数通过 `_to_trinity_config()` 将 AgentScope 配置转换为 Trinity-RFT 配置。为什么不在 AgentScope 内部实现训练算法？

**Q4**: `JudgeType` 签名中的 `auxiliary_models` 参数有什么用途？举一个实际使用场景。

### 挑战题

**Q5**: 设计一个多 Agent 协作调优方案：一个 Agent 负责生成方案，另一个 Agent 负责审查方案。如何用 WorkflowType 和 JudgeType 表达这个训练流程？

---

### 参考答案

**A1**: 互斥是因为它们代表两种不同的 RL 信号来源。直接奖励（`reward ≠ None`）适用于结果可自动评分的场景（如代码通过测试用例的数量）。Judge 评估（`response ≠ None`）适用于需要主观判断的场景（如回答质量），由 LLM 或人工评估给出奖励。

**A2**: GRPO 的核心思想是在一个 group 内比较多个 rollout 的质量。`group_size=8` 意味着对同一个任务生成 8 个不同的响应，组内按奖励排序，最好的获得正向梯度信号，最差获得负向。这避免了需要单独的 value network 来估计基线。

**A3**: 分离关注点——AgentScope 专注于 Agent 框架（编排、工具、记忆），Trinity-RFT 专注于 RL 训练（分布式训练、梯度计算、检查点管理）。这让两个框架各自独立演进，用户也可以使用其他训练框架替换 Trinity-RFT。同时，训练算法涉及 GPU 管理、分布式计算等复杂基础设施，不适合在 Agent 框架中实现。

**A4**: `auxiliary_models` 用于 LLM-as-Judge 模式。实际场景：评估客户服务回答的"同理心"指标时，使用一个专门的评判模型（如 GPT-4）来评估回答是否体现了同理心。这个评判模型通过 `auxiliary_models` 传入，避免在 Judge 函数中硬编码 API 调用。

**A5**: 工作流中同时管理两个 Agent：生成 Agent 产生方案，审查 Agent 审查并给出反馈。WorkflowOutput 的 response 包含完整的多轮交互记录。Judge 评估最终方案的质量和审查反馈的有效性。关键是 `auxiliary_models` 可以传入审查 Agent 使用的模型，使其成为可调优的一部分。

---

## 模块小结

| 概念 | 要点 |
|------|------|
| tune() | 统一训练入口，委托给 Trinity-RFT |
| WorkflowType | 异步可调用，双输出设计（直接奖励 vs Judge 评估） |
| JudgeType | 异步可调用，返回标量奖励 + 可选指标 |
| AlgorithmConfig | GRPO/SFT 算法配置 |
| DatasetConfig | HuggingFace 数据集配置，支持流式预览 |
| TunerModelConfig | 被调优模型的完整配置 |
| TinkerConfig | LoRA 低秩适应配置 |

## 章节关联

| 相关模块 | 关联点 |
|----------|--------|
| [Evaluate 模块](module_evaluate_deep.md) | 评估发现问题，调优解决问题 |
| [Model 模块](module_model_deep.md) | 被调优的模型通过 Model 层提供服务 |
| [Agent 模块](module_agent_deep.md) | Agent 行为是调优的目标 |
| [Config 模块](module_config_deep.md) | 模型和训练配置管理 |

**版本参考**: AgentScope >= 1.0.0 | 源码 `tuner/`
