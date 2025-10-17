# Training agent workflows with RL using Trinity-RFT

AgentScope exposes a `learn` interface to train agent workflows using reinforcement learning (RL).
The `learn` interface leverages Trinity-RFT so you can start training with only a small workflow function that returns a reward signal.


> [Trinity-RFT](https://github.com/modelscope/Trinity-RFT) is a flexible, general-purpose framework for reinforcement fine-tuning (RFT) of large language models (LLMs).
> It integrates well with agent frameworks and supports training agents with minimal code changes.

---

## How to implement

To train an agent workflow using RL, implement a workflow function with the
following signature:

```python
def workflow_function(
    task: Dict,
    model: TrinityChatModel,
) -> float:
    """Run the agent workflow on a single task and return a scalar reward.

    Args:
        task (Dict): Input data for the workflow (for example, contains 'question' and
            'answer' keys for a math problem).
        model (TrinityChatModel): The model instance used by the agent.

    Returns:
        float: A scalar reward measuring the agent's performance on the task.
    """
```

Below is a simplified example that uses `ReActAgent` to solve math problems. For the
full implementation see [train_react.py](./train_react.py).

```python
from typing import Dict

from pydantic import BaseModel, Field

from agentscope.learn import learn, LearnConfig
from agentscope.model import TrinityChatModel
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg


class ResponseStructure(BaseModel):
    """Response structure for math tasks (simplified)."""

    result: str = Field(description="Final answer to the math problem.")


def calculate_reward(answer: str, truth: str) -> float:
    """Simple reward: 1.0 for exact match, else 0.0.

    This is a toy reward function; replace it with a more robust metric if needed.
    """

    return 1.0 if answer.strip() == truth.strip() else 0.0


async def react_agent_workflow(task: Dict, model: TrinityChatModel) -> float:
    """Workflow function for ReAct agent training.
    """

    sys_prompt = "You are a helpful math problem solving agent."  # Example system prompt
    formatter = OpenAIChatFormatter()
    # Make sure `sys_prompt` is defined in your script or config before using it
    agent = ReActAgent(
        name="react_agent",
        sys_prompt=sys_prompt,
        model=model,
        enable_meta_tool=True,
        formatter=formatter,
    )

    response = await agent.reply(
        msg=Msg("user", task["question"], role="user"),
        structured_model=ResponseStructure,
    )

    reward = calculate_reward(response.metadata["result"], task["answer"])
    return reward


if __name__ == "__main__":
    learn(
        workflow_func=react_agent_workflow,
        config=LearnConfig.load_config("/path/to/your/config.yaml"),
    )
```

The training logic is implemented inside `react_agent_workflow`. The agent
implementation itself doesn't need to change — only the workflow function must
return a reward signal that Trinity-RFT will use for reinforcement fine-tuning.

For configuration details, see the Trinity-RFT configuration guide:
[Trinity-RFT configuration guide](https://modelscope.github.io/Trinity-RFT/en/main/tutorial/trinity_configs.html)

---

## How to run

1. Prerequisites

    - At least 2 NVIDIA GPUs with CUDA 12.4 or newer.
    - Adjust the configuration file ([gsm8k.yaml](./gsm8k.yaml)) based on your hardware.
    - Follow the Trinity-RFT [installation guide](https://modelscope.github.io/Trinity-RFT/en/main/tutorial/trinity_installation.html) to install a compatible version (Trinity-RFT >= 0.3.1).
    - Download the GSM8K dataset and Qwen/Qwen3-8B model checkpoints (example):

      ```bash
      huggingface-cli download openai/gsm8k --repo-type dataset
      huggingface-cli download Qwen/Qwen3-8B
      ```

2. Set up a Ray cluster

    ```bash
    ray start --head
    # for multi-node setup, run the following command on worker nodes
    # ray start --address=<master_address>
    ```

3. Run the training script

    ```bash
    python train_react.py
    ```
