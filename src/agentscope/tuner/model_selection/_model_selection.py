# -*- coding: utf-8 -*-
"""Model selection module for selecting the best performing model from
candidates based on evaluation metrics."""
import asyncio
import logging
from typing import Sequence, Tuple
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
) -> Tuple[ChatModelBase, dict[str, float]]:
    """
    Select the best performing model from candidate models based on evaluation
    metrics on a dataset.

    Args:
        workflow_func (`WorkflowType`):
            The workflow function that executes the task with a given model.
            The workflow may contain multiple nodes that use different models.
            Models to be selected by select_model should be defined with
            "model" as the main parameter in the workflow_func. Other models
            that don't require optimization should be passed via
            auxiliary_models parameter.
        judge_func (`JudgeType`):
            The judge function that evaluates the output of the workflow. This
            function is user-defined and needs to parse the corresponding
            WorkflowOutput. The function should return reward values where
            higher values indicate better performance by default.
        train_dataset (`DatasetConfig`):
            Configuration of the dataset used for model evaluation.
        candidate_models (`Sequence[ChatModelBase]`):
            A sequence of candidate models to evaluate.
        auxiliary_models (`dict[str, ChatModelBase] | None`):
            Auxiliary models used by the judge function, if any. Defaults to
            None.

    Returns:
        `Tuple[ChatModelBase, dict[str, float]]`: A tuple containing:
            - The model that achieved the best performance across the dataset
              (with the highest average reward)
            - Dictionary of aggregated metrics collected during evaluation
    """
    if len(candidate_models) < 2:
        raise ValueError("At least two candidate models must be provided.")

    logger.info(
        "Evaluating %d candidate models: %s",
        len(candidate_models),
        [model.model_name for model in candidate_models],
    )

    # Set up initial best value to look for the highest reward (always maximize)
    best_avg_reward = float("-inf")  # Look for largest reward

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
            range(min(train_dataset.total_steps, len(dataset))),
        )
    else:
        # Repeat dataset for total_epochs if total_steps not specified
        if train_dataset.total_epochs > 1:
            import itertools

            dataset = itertools.chain.from_iterable(
                itertools.repeat(dataset, train_dataset.total_epochs),
            )

    best_model = candidate_models[0] if candidate_models else None
    model_scores = {}  # Track scores for each model to provide visibility
    all_metrics = {}  # Collect metrics from the best model evaluation

    for model in candidate_models:
        logger.info("Evaluating model: %s", model.model_name)

        total_reward = 0.0
        num_samples = 0
        model_metrics: dict[str, float] = (
            {}
        )  # Store accumulated metrics for this model

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
                sample,
                model,
                auxiliary_models or {},
            )
            end_time = asyncio.get_event_loop().time()

            # Add timing information to metrics
            execution_time = end_time - start_time
            if workflow_output.metrics is None:
                workflow_output.metrics = {}
            workflow_output.metrics["execution_time"] = execution_time

            # Evaluate the workflow output using judge function
            judge_output: JudgeOutput = await judge_func(
                sample,
                workflow_output,
                auxiliary_models or {},
            )

            total_reward += judge_output.reward
            num_samples += 1

            # Aggregate metrics from this sample
            if judge_output.metrics:
                for key, value in judge_output.metrics.items():
                    if key in model_metrics:
                        model_metrics[key] += value
                    else:
                        model_metrics[key] = value

        avg_reward = total_reward / num_samples if num_samples > 0 else 0.0
        model_scores[model.model_name] = avg_reward

        # Average the metrics per sample for this model
        averaged_model_metrics = {}
        for key, value in model_metrics.items():
            averaged_model_metrics[f"{key}_avg"] = (
                value / num_samples if num_samples > 0 else 0.0
            )

        logger.info(
            "Model '%s' completed evaluation with average performance: %.4f",
            model.model_name,
            avg_reward,
        )

        # Update best model if current model performs better
        if avg_reward > best_avg_reward:
            best_avg_reward = avg_reward
            best_model = model
            all_metrics = (
                averaged_model_metrics  # Store the metrics of the best model
            )

    # Report final scores for all models

    if best_model is not None:
        logger.info(
            "Selected best model: %s with score: %.4f",
            best_model.model_name,
            best_avg_reward,
        )
        return best_model, all_metrics
    else:
        raise RuntimeError("No best model selected. This should not happen.")
