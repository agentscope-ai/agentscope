# -*- coding: utf-8 -*-
"""Model selection module for selecting the best performing model from
candidates based on evaluation metrics."""
import asyncio
import logging
from typing import List, Dict, Tuple, Optional, Callable
from typing import Sequence, Union, Any
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from ...model import ChatModelBase
from .._workflow import WorkflowType, WorkflowOutput
from .._config import _check_function_signature
from .._judge import JudgeType, JudgeOutput
from .._dataset import DatasetConfig
from ...evaluate._evaluator._in_memory_exporter import _InMemoryExporter


logger = logging.getLogger(__name__)


def check_workflow_function(
    func: Callable,
) -> None:
    """Check if the given function is a valid JudgeType.

    Args:
        func (Callable): The function to check.
    """
    essential_params = ["task", "model"]
    _check_function_signature(
        func,
        essential_params,
    )


def check_judge_function(
    func: Callable,
) -> None:
    """Check if the given function is a valid JudgeType.

    Args:
        func (Callable): The function to check.
    """
    try:
        _check_function_signature(func, ["task", "response"])
    except Exception:
        _check_function_signature(func, ["_task", "response"])


async def _load_dataset(
    train_dataset: DatasetConfig,
) -> Any:
    """Load and optionally limit dataset."""
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError(
            "Please install with `pip install datasets`",
        ) from e

    dataset = load_dataset(
        path=train_dataset.path,
        name=train_dataset.name,
        split=train_dataset.split,
    )

    if train_dataset.total_steps is not None:
        dataset = dataset.select(
            range(min(train_dataset.total_steps, len(dataset))),
        )
    return dataset


async def select_model(
    *,
    workflow_func: WorkflowType,
    judge_func: JudgeType,
    train_dataset: DatasetConfig,
    candidate_models: Sequence[ChatModelBase],
    max_threads: int = 2,
) -> Tuple[ChatModelBase, Dict[str, float]]:
    """
    Select the best performing model from candidate models based on evaluation
    metrics on a dataset.

    Args:
        workflow_func (`WorkflowType`):
            The workflow function that executes the task with a given model.
            The workflow may contain multiple nodes that use different models.
            Models to be selected should be defined with
            "model" as the main parameter in the workflow_func.
        judge_func (`JudgeType`):
            The judge function that evaluates the output of the workflow. This
            function is user-defined and needs to parse the corresponding
            WorkflowOutput. The function should return reward values where
            higher values indicate better performance by default.
        train_dataset (`DatasetConfig`):
            Configuration of the dataset used for model evaluation.
        candidate_models (`Sequence[ChatModelBase]`):
            A sequence of candidate models to evaluate.
        max_threads (`int`, optional):
            Maximum number of concurrent evaluations. Defaults to 2.

    Returns:
        `Tuple[ChatModelBase, dict[str, float]]`: A tuple containing:
            - The model that achieved the best performance across the dataset
              (with the highest average reward)
            - Dictionary of aggregated metrics collected during evaluation
    """
    check_workflow_function(workflow_func)
    check_judge_function(judge_func)
    if len(candidate_models) < 2:
        raise ValueError("At least two candidate models must be provided.")

    logger.info(
        "Evaluating %d candidate models: %s",
        len(candidate_models),
        [model.model_name for model in candidate_models],
    )

    # Setup OpenTelemetry tracing with the in-memory exporter once globally
    exporter = _InMemoryExporter()
    span_processor = SimpleSpanProcessor(exporter)

    # Create and configure tracer provider for the entire evaluation
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(span_processor)

    # Set our custom tracer provider for this evaluation
    trace.set_tracer_provider(tracer_provider)

    best_avg_reward = float("-inf")  # Look for largest reward

    dataset = await _load_dataset(train_dataset)

    best_model = candidate_models[0] if candidate_models else None
    model_scores = {}  # Track scores for each model to provide visibility
    model_detailed_metrics = {}  # Track detailed metrics for each model
    all_metrics = {}  # Collect metrics from the best model evaluation

    for model in candidate_models:
        logger.info("Evaluating model: %s", model.model_name)

        total_reward = 0.0
        num_samples = 0
        model_metrics: Dict[str, float] = {}
        # Store accumulated metrics for this model

        # Process dataset samples with async function calls
        semaphore = asyncio.Semaphore(max_threads)

        async def evaluate_with_semaphore(
            idx: int,
            sample: dict,
            model: ChatModelBase = model,
            exporter: _InMemoryExporter = exporter,
            sem: asyncio.Semaphore = semaphore,
        ) -> Optional[JudgeOutput]:
            async with sem:
                try:
                    # Process this sample using the new async function
                    judge_output = await _evaluate_single_sample(
                        sample=sample,
                        model=model,
                        workflow_func=workflow_func,
                        judge_func=judge_func,
                        exporter=exporter,
                    )
                    return judge_output

                except Exception as e:
                    logger.warning(
                        "Skipping sample %d for model %s due to error: %s",
                        idx,
                        model.model_name,
                        str(e),
                    )
                    return None

        # Create tasks for all samples
        tasks = [
            evaluate_with_semaphore(idx, sample)
            for idx, sample in enumerate(dataset)
            if (
                train_dataset.total_steps is None
                or idx < train_dataset.total_steps
            )
        ]

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        (
            total_reward,
            num_samples,
            averaged_model_metrics,
        ) = _process_evaluation_results(
            results,
            model_metrics,
            total_reward,
            num_samples,
        )

        avg_reward = total_reward / num_samples if num_samples > 0 else 0.0
        model_scores[model.model_name] = avg_reward

        # Save detailed metrics for this model
        model_detailed_metrics[model.model_name] = averaged_model_metrics

        logger.info(
            "Model '%s' completed evaluation with average performance: %.4f",
            model.model_name,
            avg_reward,
        )

        # Update best model if current model performs better
        if avg_reward > best_avg_reward:
            best_avg_reward = avg_reward
            best_model = model
            all_metrics = averaged_model_metrics  # Store metrics of best model

    # Report final scores and detailed metrics for all models
    logger.info("Model evaluation results:")
    for model_name, avg_score in model_scores.items():
        logger.info("  %s: %.4f", model_name, avg_score)

    logger.info("Detailed metrics for all models:")
    for model_name, metrics in model_detailed_metrics.items():
        logger.info("Metrics for %s:", model_name)
        for metric_name, metric_value in metrics.items():
            logger.info("  %s: %s", metric_name, metric_value)

    # Show the selected best model
    if best_model is not None:
        logger.info("Selected best model: %s", best_model.model_name)
        return best_model, all_metrics
    else:
        raise RuntimeError("No best model selected. This should not happen.")


def _process_evaluation_results(
    results: List[Union[JudgeOutput, BaseException, None]],
    model_metrics: Dict[str, float],
    total_reward_init: float,
    num_samples_init: int,
) -> Tuple[float, int, Dict[str, float]]:
    """
    Process evaluation results to calculate total reward and aggregate metrics.

    Args:
        results: The list of evaluation results
        model_metrics: Dictionary to accumulate metrics
        total_reward_init: Initial value for total reward
        num_samples_init: Initial value for number of samples

    Returns:
        Tuple of (total_reward, num_samples, averaged_model_metrics)
    """
    import numbers

    total_reward = total_reward_init
    num_samples = num_samples_init

    for result in results:
        if result is None or isinstance(result, Exception):
            if isinstance(result, Exception):
                logger.warning(
                    "Sample evaluation failed: %s",
                    str(result),
                )
            continue  # Skip failed evaluations and don't count in num_samples

        # Only count results that are JudgeOutput toward num_samples
        # Ensure it's actually a JudgeOutput before accessing attributes
        if not isinstance(result, JudgeOutput):
            continue  # Skip non-JudgeOutput results

        # Only count results that are not None toward num_samples
        total_reward += result.reward
        num_samples += 1

        # Aggregate metrics from this sample
        if result.metrics:
            for key, value in result.metrics.items():
                if key in model_metrics:
                    model_metrics[key] += value
                else:
                    model_metrics[key] = value

    # Average the metrics per sample for this model
    averaged_model_metrics = {}
    for key, value in model_metrics.items():
        if isinstance(value, numbers.Number):
            num_val = value.real if hasattr(value, "real") else 0.0
            averaged_model_metrics[f"{key}_avg"] = (
                float(num_val) / num_samples if num_samples > 0 else 0.0
            )

    return total_reward, num_samples, averaged_model_metrics


async def _evaluate_single_sample(
    sample: dict,
    model: ChatModelBase,
    workflow_func: WorkflowType,
    judge_func: JudgeType,
    exporter: _InMemoryExporter,
) -> JudgeOutput:
    """
    Evaluate a single sample with the given model and workflow/judge functions.

    Args:
        sample (dict): The sample to evaluate
        model (ChatModelBase): The model to use for evaluation
        workflow_func (WorkflowType): The workflow function to execute
        judge_func (JudgeType): The judge function to evaluate the result
        exporter (_InMemoryExporter): The exporter to collect traces

    Returns:
        JudgeOutput: The output from the judge function
    """
    # Create a unique task ID for this sample evaluation
    import uuid
    from opentelemetry import baggage
    from opentelemetry.context import attach, detach

    task_id = f"eval_task_{uuid.uuid4().hex[:8]}"
    repeat_id = "0"

    # Execute workflow with current model and measure execution time
    start_time = asyncio.get_event_loop().time()

    # Setup the tracer with baggage for the exporter to track this task
    tracer = trace.get_tracer(__name__)

    # Set baggage (this is critical for exporter to associate spans with tasks)
    ctx = baggage.set_baggage("task_id", task_id)
    ctx = baggage.set_baggage("repeat_id", repeat_id, context=ctx)

    # Activate the context
    token = attach(ctx)

    try:
        with tracer.start_as_current_span(
            name=f"Solution_{task_id}_{repeat_id}",
        ):
            # Access _config for trace enablement
            from ... import _config

            _config.trace_enabled = True

            # Execute workflow with current model
            workflow_output: WorkflowOutput = await workflow_func(
                task=sample,
                model=model,
            )
    finally:
        detach(token)

    end_time = asyncio.get_event_loop().time()

    # Add timing information to metrics
    execution_time = end_time - start_time
    if workflow_output.metrics is None:
        workflow_output.metrics = {}
    workflow_output.metrics["execution_time"] = execution_time

    # Extract token usage information from the exporter
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0

    # Get the chat usage data from the exporter
    if task_id in exporter.cnt and repeat_id in exporter.cnt[task_id]:
        chat_usage_data = exporter.cnt[task_id][repeat_id].get(
            "chat_usage",
            {},
        )
        # Sum up token usage across all models used in this task
        for _, usage in chat_usage_data.items():  # Fixed: unused variable
            total_input_tokens += int(usage.get("input_tokens", 0))
            total_output_tokens += int(usage.get("output_tokens", 0))

    total_tokens = total_input_tokens + total_output_tokens

    # Add usage information to workflow_output metrics
    workflow_output.metrics["input_tokens"] = float(total_input_tokens)
    workflow_output.metrics["output_tokens"] = float(total_output_tokens)
    workflow_output.metrics["total_tokens"] = float(total_tokens)

    # Evaluate the workflow output using judge function
    # Pass a composite dict containing both the response and workflow metrics
    judge_output: JudgeOutput = await judge_func(
        task=sample,
        response={
            "response": workflow_output.response,
            "metrics": workflow_output.metrics,
        },
    )

    return judge_output
