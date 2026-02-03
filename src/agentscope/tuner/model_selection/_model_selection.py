# -*- coding: utf-8 -*-
"""Model selection module for selecting the best performing model from candidates based on evaluation metrics."""
import asyncio
import logging
from typing import Sequence, Callable
from ...model import ChatModelBase
from .._workflow import WorkflowType, WorkflowOutput
from .._judge import JudgeType, JudgeOutput
from .._dataset import DatasetConfig

logger = logging.getLogger(__name__)


async def select_model(
    *,
    workflow_func: WorkflowType,
    judge_func: JudgeType,
    train_dataset: DatasetConfig,
    candidate_models: Sequence[ChatModelBase],
    auxiliary_models: dict[str, ChatModelBase] | None = None,
) -> ChatModelBase:
    """
    Select the best performing model from candidate models based on evaluation metrics on a dataset.

    Args:
        workflow_func (`WorkflowType`):
            The workflow function that executes the task with a given model.
            The workflow may contain multiple nodes that use different models.
            Models to be selected by select_model should be defined with "model" as the
            main parameter in the workflow_func. Other models that don't require
            optimization should be passed via auxiliary_models parameter.
        judge_func (`JudgeType`):
            The judge function that evaluates the output of the workflow. This
            function is user-defined and needs to parse the corresponding
            WorkflowOutput. The function should return reward values where
            higher values indicate better performance by default. However, for
            built-in functions like avg_time_judge and avg_token_consumption_judge,
            lower values (more negative) indicate better performance, so the
            selection logic will adapt accordingly.
        train_dataset (`DatasetConfig`):
            Configuration of the dataset used for model evaluation.
        candidate_models (`Sequence[ChatModelBase]`):
            A sequence of candidate models to evaluate.
        auxiliary_models (`dict[str, ChatModelBase] | None`):
            Auxiliary models used by the judge function, if any. Defaults to None.

    Returns:
        `ChatModelBase`: The model that achieved the best performance across the dataset.
                         For custom judge functions (higher reward is better), the model with
                         the highest average reward is returned. For built-in judge functions
                         (lower consumption is better), the model with the lowest average
                         (most negative) reward is returned.
    """
    if not candidate_models:
        raise ValueError("At least one candidate model must be provided.")

    logger.info(
        f"Evaluating {len(candidate_models)} candidate models: {[model.model_name for model in candidate_models]}"
    )

    # Determine if we're using built-in judge functions that favor lower values
    # We check if the judge function is one of the known built-in functions
    is_minimization_task = (
        judge_func.__name__
        in ["avg_time_judge", "avg_token_consumption_judge"]
        if hasattr(judge_func, "__name__")
        else False
    )

    # Set up initial best value depending on whether we're maximizing or minimizing
    if is_minimization_task:
        best_avg_reward = float(
            "inf"
        )  # Look for smallest (most negative) reward
        compare_better = lambda current, best: current < best
    else:
        best_avg_reward = float("-inf")  # Look for largest reward
        compare_better = lambda current, best: current > best

    # Load dataset

    from datasets import load_dataset

    dataset = load_dataset(
        path=train_dataset.path,
        name=train_dataset.name,
        split=train_dataset.split,
    )

    # Limit dataset if total_steps is specified
    if train_dataset.total_steps is not None:
        dataset = dataset.select(
            range(min(train_dataset.total_steps, len(dataset)))
        )
    else:
        # Repeat dataset for total_epochs if total_steps not specified
        if train_dataset.total_epochs > 1:
            import itertools

            dataset = itertools.chain.from_iterable(
                itertools.repeat(dataset, train_dataset.total_epochs)
            )

    best_model = candidate_models[0] if candidate_models else None
    model_scores = {}  # Track scores for each model to provide visibility

    for model in candidate_models:
        logger.info(f"Evaluating model: {model.model_name}")

        total_reward = 0.0
        num_samples = 0

        # Iterate through dataset
        for idx, sample in enumerate(dataset):
            if (
                train_dataset.total_steps is not None
                and idx >= train_dataset.total_steps
            ):
                break

            # Execute workflow with current model and measure execution time
            start_time = asyncio.get_event_loop().time()
            workflow_output: WorkflowOutput = await workflow_func(
                sample, model, auxiliary_models or {}
            )
            end_time = asyncio.get_event_loop().time()

            # Add timing information to metrics
            execution_time = end_time - start_time
            if workflow_output.metrics is None:
                workflow_output.metrics = {}
            workflow_output.metrics["execution_time"] = execution_time

            # Evaluate the workflow output using judge function
            judge_output: JudgeOutput = await judge_func(
                sample, workflow_output, auxiliary_models or {}
            )

            total_reward += judge_output.reward
            num_samples += 1

        avg_reward = total_reward / num_samples if num_samples > 0 else 0.0
        model_scores[model.model_name] = avg_reward

        logger.info(
            f"Model '{model.model_name}' completed evaluation with average performance: {avg_reward:.4f}"
        )

        # Update best model if current model performs better
        if compare_better(avg_reward, best_avg_reward):
            best_avg_reward = avg_reward
            best_model = model

    # Report final scores for all models

    if best_model is not None:
        logger.info(
            f"Selected best model: {best_model.model_name} with score: {best_avg_reward:.4f}"
        )
    else:
        logger.warning("No best model selected - this should not happen")

    return best_model
