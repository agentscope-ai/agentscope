# AgentLoop Integration Example

This example demonstrates how to use the AgentLoop integration:
1. Load evaluation datasets from Alibaba Cloud AgentLoop
2. Write Experiment results to SLS (Log Service)

## Quick Start

### Prerequisites

1. **Install dependencies**

```bash
pip install agentscope[agentloop]
```

2. **Prepare your credentials**

You need Alibaba Cloud Access Key ID and Secret with permissions to access CMS and SLS.

### Running the Example

1. **Edit `main.py`** and replace the placeholder values:

```python
await run_agentloop_experiment(
    ak="replace with your ak",
    sk="replace with your sk",
    workspace="replace with your workspace",
    dataset="replace with your dataset name",
    region_id="replace with your region_id",
    result_dir="./results",
)
```

2. **Implement your agent logic in the solution function**:

```python
async def http_agent_solution(
    task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    output = ""  # Call your agent and get the output

    return SolutionOutput(
        success=True,
        output=output,
        trajectory=[],
        meta={"task": task},
    )
```

3. **Run the evaluation**

```bash
cd examples/evaluation/agentloop_demo
python main.py
```

## Configuration

The `AgentLoopConfig` class is used to configure both dataset loading and result storage:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `workspace` | Yes | CMS workspace name |
| `dataset` | Yes | CMS dataset name |
| `region_id` | Yes | Alibaba Cloud region ID (e.g., `cn-hangzhou`) |
| `access_key_id` | Yes | Alibaba Cloud Access Key ID |
| `access_key_secret` | Yes | Alibaba Cloud Access Key Secret |