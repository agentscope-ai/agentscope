# Tuner 调优系统

> **Level 7**: 能独立开发模块
> **前置要求**: [Evaluate 评估系统](./09-evaluate-system.md)
> **后续章节**: [Runtime 服务化](../10-deployment/10-runtime.md)

---

## 学习目标

学完本章后，你能：
- 理解 AgentScope 的 Tuner 调优框架设计
- 掌握 tune() 函数的核心参数
- 理解 Workflow、Judge、Dataset 的关系
- 知道如何配置 monitor 进行训练监控

---

## 背景问题

当 Agent 开发完成后，如何优化其性能？Tuner 系统提供了对 Agent 工作流进行微调的能力，基于：
1. **Workflow**: 定义 Agent 的工作流程
2. **Judge**: 评估输出质量
3. **Dataset**: 提供训练和评估数据
4. **Algorithm**: 指定调优算法

---

## 源码入口

| 项目 | 值 |
|------|-----|
| **目录** | `src/agentscope/tuner/` |
| **核心函数** | `tune()` |
| **依赖** | Trinity-RFT (`pip install trinity-rft`) |

---

## 核心架构

### 调优流程

```mermaid
flowchart TD
    WORKFLOW[workflow_func<br/>定义 Agent 工作流] --> TUNE[tune()<br/>调优入口]
    JUDGE[judge_func<br/>评估函数] --> TUNE
    TRAIN[train_dataset<br/>训练数据] --> TUNE
    EVAL[eval_dataset<br/>评估数据] --> TUNE
    MODEL[model config<br/>模型配置] --> TUNE
    ALGORITHM[algorithm config<br/>调优算法] --> TUNE

    TUNE --> TRINITY[Trinity-RFT<br/>执行微调]
    TRINITY --> MONITOR[Monitor<br/>tensorboard/wandb/mlflow]
```

---

## tune() 函数

**文件**: `src/agentscope/tuner/_tune.py:16-96`

```python
def tune(
    *,
    workflow_func: WorkflowType,
    judge_func: JudgeType | None = None,
    train_dataset: DatasetConfig | None = None,
    eval_dataset: DatasetConfig | None = None,
    model: TunerModelConfig | None = None,
    auxiliary_models: dict[str, TunerModelConfig] | None = None,
    algorithm: AlgorithmConfig | None = None,
    project_name: str | None = None,
    experiment_name: str | None = None,
    monitor_type: str | None = None,
    config_path: str | None = None,
) -> None:
```

### 参数详解

| 参数 | 类型 | 说明 |
|------|------|------|
| `workflow_func` | `WorkflowType` | **必需**。定义 Agent 工作流的函数 |
| `judge_func` | `JudgeType` | Judge 函数，用于评估输出质量 |
| `train_dataset` | `DatasetConfig` | 训练数据集配置 |
| `eval_dataset` | `DatasetConfig` | 评估数据集配置 |
| `model` | `TunerModelConfig` | 要调优的模型配置 |
| `auxiliary_models` | `dict` | 辅助模型（如 LLM-as-Judge） |
| `algorithm` | `AlgorithmConfig` | 调优算法配置 |
| `project_name` | `str` | 项目名称 |
| `experiment_name` | `str` | 实验名称（默认用时间戳） |
| `monitor_type` | `str` | 监控类型：`tensorboard`/`wandb`/`mlflow`/`swanlab` |
| `config_path` | `str` | YAML 配置文件路径 |

---

## 核心配置类

### TunerModelConfig

**文件**: `src/agentscope/tuner/_model.py`

```python
@dataclass
class TunerModelConfig:
    model_name: str           # 模型名称
    model_path: str | None  # 模型路径
    api_key: str | None     # API Key
    base_url: str | None    # 自定义 API 地址
    ...  # 其他模型参数
```

### DatasetConfig

**文件**: `src/agentscope/tuner/_dataset.py`

```python
@dataclass
class DatasetConfig:
    name: str                    # 数据集名称
    path: str                    # 数据集路径
    data_format: str             # 数据格式
    ...
```

### AlgorithmConfig

**文件**: `src/agentscope/tuner/_algorithm.py`

```python
@dataclass
class AlgorithmConfig:
    name: str        # 算法名称
    params: dict    # 算法参数
```

---

## 使用示例

### 基本用法

```python
from agentscope.tuner import tune, TunerModelConfig, DatasetConfig

# 定义 Agent 工作流
async def my_workflow(task, callback):
    agent = ReActAgent(model=model, toolkit=toolkit)
    response = await agent(Msg("user", task, "user"))
    return response.content

# 调优配置
model_config = TunerModelConfig(
    model_name="gpt-4o",
    api_key=os.environ["OPENAI_API_KEY"],
)

dataset_config = DatasetConfig(
    name="my_dataset",
    path="./data/train.jsonl",
    data_format="jsonl",
)

# 执行调优
tune(
    workflow_func=my_workflow,
    model=model_config,
    train_dataset=dataset_config,
    project_name="my_agent",
    experiment_name="v1",
    monitor_type="tensorboard",
)
```

### 带 Judge 的用法

```python
from agentscope.tuner import tune, JudgeType

def my_judge(output, ground_truth):
    """评估 Agent 输出质量"""
    return {"score": 1.0 if output == ground_truth else 0.0}

tune(
    workflow_func=my_workflow,
    judge_func=my_judge,
    train_dataset=train_config,
    eval_dataset=eval_config,
    algorithm=AlgorithmConfig(name="ppo", params={"lr": 1e-4}),
)
```

---

## Monitor 支持

**文件**: `src/agentscope/tuner/_config.py`

| Monitor | 说明 | 安装 |
|---------|------|------|
| `tensorboard` | 默认选项 | `pip install tensorboard` |
| `wandb` | Weights & Biases | `pip install wandb` |
| `mlflow` | MLflow Tracking | `pip install mlflow` |
| `swanlab` | SwanLab | `pip install swanlab` |

---

## 工程现实与架构问题

### 技术债 (源码级)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| `_tune.py:50` | tune() 无本地训练模式 | 无法在没有 Trinity 服务时本地调优 | 高 |
| `_config.py:100` | YAML 配置无 Schema 验证 | 格式错误在运行时才报错 | 中 |
| `_config.py:150` | monitor 配置无连接验证 | monitor 服务不可用时静默失败 | 中 |
| `_tune.py:80` | 无训练中断恢复机制 | 中断后需要从头开始 | 中 |
| `_model.py:50` | TunerModelConfig 无默认参数 | 不同模型需要不同配置但无提示 | 低 |

**[HISTORICAL INFERENCE]**: Tuner 模块依赖外部 Trinity-RFT 服务，设计时假设服务总是可用的。实际使用中，本地训练和断点恢复是强烈需求。

### 性能考量

```python
# Tuner 调优开销估算
单次 workflow 运行: 取决于 Agent 复杂度 (~10s-10min)
Trinity-RFT API 调用: ~1-5s overhead
Monitor 数据写入: ~100ms/point

# 调优实验规模
小规模 (10 trials): ~10 × 10s-10min = ~2min-2hr
中规模 (100 trials): ~100 × 10s-10min = ~20min-20hr
大规模 (1000 trials): 需要 Ray 分布式
```

### Trinity 服务依赖问题

```python
# 当前问题: 无本地训练模式
def tune(workflow_func, ...):
    # 直接调用 Trinity-RFT，如果服务不可用则失败
    config = _to_trinity_config(workflow_func, ...)
    run_stage(config)  # 假设 Trinity 服务运行中

# 解决方案: 添加本地回退
def tune(workflow_func, ..., local_mode: bool = False):
    if local_mode:
        return _local_tune(workflow_func, ...)
    else:
        config = _to_trinity_config(workflow_func, ...)
        run_stage(config)
```

### 渐进式重构方案

```python
# 方案 1: 添加本地训练模式
async def _local_tune(
    workflow_func: WorkflowType,
    train_dataset: DatasetConfig,
    model: TunerModelConfig,
    algorithm: AlgorithmConfig,
    n_trials: int = 10,
):
    """本地执行调优（不依赖 Trinity 服务）"""
    best_params = None
    best_score = float('-inf')

    for trial in range(n_trials):
        # 生成超参数
        params = algorithm.sample_params()

        # 运行 workflow
        results = []
        for task in train_dataset:
            output = await workflow_func(task, params)
            score = evaluate(output, task.ground_truth)
            results.append(score)

        # 计算平均分数
        avg_score = sum(results) / len(results)

        if avg_score > best_score:
            best_score = avg_score
            best_params = params

        logger.info(f"Trial {trial}: score = {avg_score:.4f}")

    return best_params

# 方案 2: 添加训练中断恢复
class ResumableTuner:
    def __init__(self, checkpoint_dir: str):
        self._checkpoint_dir = checkpoint_dir

    async def tune(self, workflow_func, ...):
        # 检查是否有 checkpoint
        checkpoint_path = f"{self._checkpoint_dir}/checkpoint.json"
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path) as f:
                state = json.load(f)
                start_trial = state["completed_trials"]
                best_params = state["best_params"]
        else:
            start_trial = 0
            best_params = None

        for trial in range(start_trial, n_trials):
            result = await self._run_trial(workflow_func, trial)

            # 保存 checkpoint
            with open(checkpoint_path, "w") as f:
                json.dump({
                    "completed_trials": trial + 1,
                    "best_params": result.params,
                    "best_score": result.score,
                }, f)

            if result.score > (best_params.score if best_params else 0):
                best_params = result.params
```

---

## Contributor 指南

### 调试 Tuner 问题

```python
# 1. 检查 Trinity-RFT 是否安装
try:
    from trinity.cli.launcher import run_stage
    print("Trinity-RFT installed")
except ImportError:
    print("Need to install: pip install trinity-rft")

# 2. 检查配置
from agentscope.tuner._config import _to_trinity_config
config = _to_trinity_config(workflow_func=my_workflow, ...)
print(f"Project: {config.project_name}")
print(f"Experiment: {config.experiment_name}")
```

### 常见问题

**问题：ImportError: Trinity-RFT is not installed**
```bash
pip install trinity-rft
```

**问题：monitor 数据未写入**
- 检查 `monitor_type` 配置是否正确
- 确认对应库已安装

### 危险区域

1. **Trinity 服务依赖**：服务不可用时完全无法调优
2. **无本地训练模式**：需要网络连接才能工作
3. **无中断恢复**：训练中断需要从头开始

---

## 下一步

接下来学习 [Runtime 服务化](../10-deployment/10-runtime.md)。


