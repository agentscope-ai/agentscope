# Training agent workflows with RL using Trinity-RFT

AgentScope exposes a `tune` interface to train agent workflows using reinforcement learning (RL).
The `tune` interface leverages [Trinity-RFT](https://github.com/modelscope/Trinity-RFT), which supports training agents with minimal code changes.

---

## How to implement

Here we use a math problem solving scenario as an example to illustrate how to convert an existing agent workflow into a trainable workflow function.

Suppose you have an agent workflow that solves math problems using the `ReActAgent`

```python
from agentscope.agent import ReActAgent

async def run_react_agent():
    # model = ...  # Initialize your ChatModel here

    query = "What is the sum of the first 10 prime numbers?"
    agent = ReActAgent(
        name="react_agent",
        sys_prompt="You are a helpful math problem solving agent.",
        model=model,
        enable_meta_tool=True,
        formatter=OpenAIChatFormatter(),
    )

    response = await agent.reply(
        msg=Msg("user", query, role="user"),
    )

    print(response)
```

### Step 1: Define a workflow function

To train an agent workflow using RL, you need to implement a workflow function with the following signature.

```python
def workflow_function(
    task: Dict,
    model: TrinityChatModel,
) -> float:
    """Run the agent workflow on a single task and return a scalar reward."""
```

Inputs:

- `task`: A dictionary representing a single training task, converted from a sample in the training dataset. For example, in a math problem solving task, `task` may contain `question` and `answer` fields.

- `model`: A `TrinityChatModel` instance, which has the same interface as `OpenAIChatModel`, but it supports automatically converting invoke history into trainable data that can be used by Trinity-RFT.

Outputs:

- A scalar reward (float) indicating the quality of the agent's response on the given task.

### Step 2: Initialize and run the agent using the provided task and model

Since the `model` has the same interface as `OpenAIChatModel`, you can directly use it to initialize the agent.

However, the `task` dictionary is a sample from the training dataset and can vary. You need to extract the relevant fields from `task` to run the agent.

Suppose your training dataset is a `.jsonl` file with samples like:

```json
{"question": "What is 2 + 2?", "answer": "4"}
{"question": "What is 4 + 4?", "answer": "8"}
```

In this case, you can extract the `question` field from `task` to run the agent:

```python
def workflow_function(
    task: Dict,
    model: TrinityChatModel,
) -> float:
    agent = ReActAgent(
        name="react_agent",
        sys_prompt="You are a helpful math problem solving agent.",
        model=model,
        enable_meta_tool=True,
        formatter=OpenAIChatFormatter(),
    )

    response = await agent.reply(
        msg=Msg("user", task["question"], role="user"),
    )

    # further steps to calculate reward... (See Step 3)
```

### Step 3: Implement a reward calculation mechanism

To train the agent using RL, you need to define a reward calculation mechanism that computes a reward based on the agent's response.

Continuing from the previous code snippet, suppose you want to give a reward of `1.0` if the agent's answer matches the ground truth answer in `task["answer"]`, and `0.0` otherwise.

```python
def calculate_reward(answer: str, truth: str) -> float:
    """Simple reward: 1.0 for exact match, else 0.0."""
    return 1.0 if answer.strip() == truth.strip() else 0.0
```

To facilitate reward calculation, you can define a structured response model that allows easy parsing of the agent's output.

```python
from pydantic import BaseModel, Field

class ResponseStructure(BaseModel):
    """Response structure for math tasks (simplified).
    This structure let the agent output be easily parsed,
    allowing for easy reward calculation.
    """

    result: str = Field(description="Final answer to the math problem.")

# ... inside workflow_function ...
#    response = await agent.reply(
#        msg=Msg("user", task["question"], role="user"),
#        structured_model=ResponseStructure,  # <-- specify structured model here
#    )
#    return calculate_reward(response.metadata["result"], task["answer"])
```

### Step 4: Use `tune` to train the workflow function

Finally, you can use the `tune` interface to train the defined workflow function with a configuration file.

```python
from agentscope.tune import tune

# your workflow function here...

if __name__ == "__main__":
    tune(
        workflow_func=workflow_function,
        config_path="/path/to/your/config.yaml",
    )
```

The trained model, training dataset, RL algorithm, training cluster and other configurations are all located in the configuration file, which should follow the Trinity-RFT configuration format.

See [config.yaml](./config.yaml) for an example configuration. For full configuration details, see [Trinity-RFT Configuration Guide](https://modelscope.github.io/Trinity-RFT/en/main/tutorial/trinity_configs.html).

---

### Complete example

```python
from typing import Dict

from pydantic import BaseModel, Field

from agentscope.tune import tune
from agentscope.model import TrinityChatModel
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg


def calculate_reward(answer: str, truth: str) -> float:
    """Simple reward: 1.0 for exact match, else 0.0.

    This is a toy reward function; replace it with a more robust metric if needed.
    """

    return 1.0 if answer.strip() == truth.strip() else 0.0


class ResponseStructure(BaseModel):
    """Response structure for math tasks (simplified).
    This structure makes the agent's output easy to parse,
    allowing for easy reward calculation.
    """

    result: str = Field(description="Final answer to the math problem.")


async def react_workflow_function(task: Dict, model: TrinityChatModel) -> float:
    """Workflow function for ReAct agent training."""

    agent = ReActAgent(
        name="react_agent",
        sys_prompt="You are a helpful math problem solving agent.",
        model=model,
        enable_meta_tool=True,
        formatter=OpenAIChatFormatter(),
    )

    response = await agent.reply(
        msg=Msg("user", task["question"], role="user"),
        structured_model=ResponseStructure,
    )

    reward = calculate_reward(response.metadata["result"], task["answer"])
    return reward


if __name__ == "__main__":
    tune(
        workflow_func=react_workflow_function,
        config_path="/path/to/your/config.yaml",
    )
```

> Above code is a simplified example for illustration purposes only.
> For a complete implementation, please refer to [main.py](./main.py).

---

## How to run

After implementing the workflow function, follow these steps to run the training:

1. Prerequisites

    - At least 2 NVIDIA GPUs with CUDA 12.8 or newer.
    - Adjust the configuration file ([config.yaml](./config.yaml)) based on your hardware.
    - Follow the Trinity-RFT [installation guide](https://modelscope.github.io/Trinity-RFT/en/main/tutorial/trinity_installation.html) to install the latest version from source code.
    - Download the GSM8K dataset and Qwen/Qwen3-8B model checkpoints (example):

      ```bash
      huggingface-cli download openai/gsm8k --repo-type dataset
      huggingface-cli download Qwen/Qwen3-8B
      ```

2. Set up a [Ray](https://github.com/ray-project/ray) cluster

    ```bash
    ray start --head
    # for multi-node setup, run the following command on worker nodes
    # ray start --address=<master_address>
    ```

3. Run the training script

    ```bash
    python main.py
    ```
