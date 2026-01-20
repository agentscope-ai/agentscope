import asyncio
from typing import Any, Sequence

from datasets import load_dataset
from pydantic import BaseModel, Field

from agentscope import logger
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._config import check_judge_function
from agentscope.tuner._dataset import DatasetConfig
from agentscope.tuner._judge import JudgeType
from agentscope.tuner._workflow import WorkflowType


class ModelSelectResult(BaseModel):
    """Result of model selection evaluation."""

    model_config = {"arbitrary_types_allowed": True}

    models: list[ChatModelBase] = Field(description="The list of models")
    rewards: list[float] = Field(
        description="The average reward for each model"
    )

    def best_model(self) -> ChatModelBase:
        """Return the model with the highest average reward."""
        best_idx = self.rewards.index(max(self.rewards))
        return self.models[best_idx]


class ModelSelectConfig(BaseModel):
    """Configuration for model selection."""
    pass


async def _evaluate_model(
    model: ChatModelBase,
    workflow_func: WorkflowType,
    judge_func: JudgeType,
    dataset: Sequence[Any],
    auxiliary_models: dict[str, ChatModelBase],
) -> float:
    """Evaluate a single model on the dataset and return average reward."""
    total_reward = 0.0
    count = 0

    for task in dataset:
        workflow_output = await workflow_func(task, model, auxiliary_models)
        judge_output = await judge_func(
            task, workflow_output.response, auxiliary_models
        )
        total_reward += judge_output.reward
        count += 1

    return total_reward / count if count > 0 else 0.0


def select_model(
    *,
    workflow_func: WorkflowType,
    judge_func: JudgeType,
    train_dataset: DatasetConfig,
    candidate_models: Sequence[ChatModelBase],
    auxiliary_models: dict[str, ChatModelBase] | None = None,
    config: ModelSelectConfig | None = None,
) -> ModelSelectResult:
    """Select the best model from candidates by evaluating on train_dataset.

    Args:
        workflow_func: An async workflow function that takes
            (task, model, auxiliary_models) and returns WorkflowOutput.
        judge_func: An async judge function that evaluates the workflow
            response and returns JudgeOutput with reward.
        train_dataset: The dataset configuration for evaluation.
        candidate_models: List of candidate models to evaluate.
        config: Optional configuration for model selection.

    Returns:
        ModelSelectResult containing models and their average rewards.
    """
    config = config or ModelSelectConfig()
    auxiliary_models = auxiliary_models or {}
    check_judge_function(judge_func)

    logger.info("Loading training dataset...")
    trainset = load_dataset(
        path=train_dataset.path,
        name=train_dataset.name,
        split=train_dataset.split,
        streaming=False,
    )
    dataset_list = list(trainset)[:50]
    logger.info(f"Loaded {len(dataset_list)} samples")

    rewards: list[float] = []

    async def evaluate_all():
        for model in candidate_models:
            logger.info(f"Evaluating model: {model.model_name}...")
            avg_reward = await _evaluate_model(
                model=model,
                workflow_func=workflow_func,
                judge_func=judge_func,
                dataset=dataset_list,
                auxiliary_models=auxiliary_models,
            )
            rewards.append(avg_reward)
            logger.info(f"Model {model.model_name} average reward: {avg_reward}")

    asyncio.run(evaluate_all())

    result = ModelSelectResult(models=list(candidate_models), rewards=rewards)
    best = result.best_model()
    logger.info(f"Best model: {best.model_name} with reward {max(rewards)}")

    return result