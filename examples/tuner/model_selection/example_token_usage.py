# -*- coding: utf-8 -*-
"""Example of model selection using agentscope tuner."""

import os
import logging
from typing import Dict, Any
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tuner import DatasetConfig
from agentscope.tuner import WorkflowOutput
from agentscope.tuner.model_selection import select_model
from agentscope.tuner.model_selection import avg_token_consumption_judge


# Configure logging to show INFO level messages
logging.basicConfig(level=logging.INFO)


# Initialize models for selection
models = [
    DashScopeChatModel(
        "qwen-turbo",
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        max_tokens=512,
    ),
    DashScopeChatModel(
        "qwen-plus", 
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        max_tokens=512,
    ),
    DashScopeChatModel(
        "qwen-max",
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        max_tokens=512,
    ),
]


async def workflow(
    task: Dict[str, Any],
    model: Any,
) -> WorkflowOutput:
    """A workflow function using the ReAct agent to solve tasks.

    Args:
        task (Dict[str, Any]): The task to be solved.
        model: The model to use for the agent.

    Returns:
        WorkflowOutput: The workflow output containing the agent's response.
    """
    agent = ReActAgent(
        name="math_solver",
        sys_prompt="You are a helpful math problem solving agent.",
        model=model,
        formatter=OpenAIChatFormatter(),
    )

    # Extract question from task
    question = (
        task.get("question", "") if isinstance(task, dict) else str(task)
    )

    # Create a message with the question
    msg = Msg(name="user", content=question, role="user")

    # Get response from the agent
    response = await agent.reply(msg=msg)

    return WorkflowOutput(
        response=response,
    )


async def main():
    # Configure the GSM8K dataset
    dataset_config = DatasetConfig(
        path="openai/gsm8k",
        name="main",
        split="test", 
        total_steps=20,  # Limit for testing purposes
    )

    # Perform model selection
    best_model, metrics = await select_model(
        workflow_func=workflow,
        judge_func=avg_token_consumption_judge,
        train_dataset=dataset_config,
        candidate_models=models,
    )

    print(f"Selected best model: {best_model.model_name}")
    print(f"Metrics: {metrics}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())