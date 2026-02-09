# -*- coding: utf-8 -*-
"""Prompt tuning functionality using DSPy's MIPROv2 optimizer."""

import os
import asyncio
from pathlib import Path
from typing import Any, Callable, Optional, cast

from agentscope.tuner import (
    DatasetConfig,
)
from agentscope import logger
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._config import _check_function_signature
from agentscope.tuner._workflow import WorkflowType
from agentscope.tuner._judge import JudgeType
from agentscope.tuner.prompt_tune._config import PromptTuneConfig
from agentscope.tuner.prompt_tune._wrapper import _WorkflowWrapperModule


def _wrap_judge_fn(judge_fn: JudgeType) -> Callable[..., float]:
    """Wrap an async judge function into a synchronous callable.

    Args:
        judge_fn: The async judge function to wrap.

    Returns:
        A synchronous wrapper function that returns only the reward value.
    """

    async def inner(
        task: dict,
        response: Any,
    ) -> float:
        # set logger to None
        output = await judge_fn(task=task, response=response)
        return output.reward

    def _sync_wrapper(
        task: dict,
        response: Any,
    ) -> float:
        return asyncio.run(inner(task=task, response=response))

    return _sync_wrapper


def _guess_by_ext(p: str) -> Optional[str]:
    """Guess the dataset format by file extension.

    Args:
        p (`str`): The file path.

    Returns:
        `Optional[str]`: The format string (e.g., 'json', 'csv') or None if
            the extension is not recognized.
    """
    pp = Path(p)
    ext = pp.suffix.lower()
    if ext in {".jsonl", ".jl"}:
        return "json"
    if ext == ".json":
        return "json"
    if ext in {".csv", ".tsv"}:
        return "csv"
    if ext in {".parquet"}:
        return "parquet"
    if ext in {".txt"}:
        return "text"
    return None


def check_workflow_function(
    func: Callable,
) -> None:
    """Check if the given function is a valid JudgeType.

    Args:
        func (Callable): The function to check.
    """
    essential_params = ["task", "system_prompt"]
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
    essential_params = ["task", "response"]
    _check_function_signature(
        func,
        essential_params,
    )

def tune_prompt(
    *,
    workflow: WorkflowType,
    init_system_prompt: str,
    judge_func: JudgeType,
    train_dataset: DatasetConfig,
    eval_dataset: DatasetConfig | None = None,
    config: PromptTuneConfig | None = None,
) -> tuple[str, dict[str, float]]:
    """Tune a system prompt using DSPy's MIPROv2 optimizer.

    This function optimizes the system prompt by leveraging DSPy's
    automatic prompt optimization capabilities.

    Args:
        workflow: An async workflow function that takes a task dict and system
            prompt string, returns a WorkflowOutput.
        init_system_prompt: The initial system prompt to be optimized.
        judge_func: An async function that evaluates the agent's response and
            returns a JudgeOutput.
        train_dataset: The dataset used for training/optimization.
        eval_dataset: Optional dataset for evaluation after optimization.
        config: Configuration for prompt tuning. Defaults to
            PromptTuneConfig().

    Returns:
        A tuple containing:
            - The optimized system prompt string.
            - A dict of metrics. May include "valset_improvement" (percentage)
              if eval_dataset is provided and config.compare_performance is
              True.
    """
    import dspy
    from datasets import load_dataset

    config = config or PromptTuneConfig()
    check_workflow_function(workflow)
    check_judge_function(judge_func)

    if os.path.exists(train_dataset.path) and _guess_by_ext(
        train_dataset.path,
    ):
        logger.info("loading dataset from file: %s", train_dataset.path)
        trainset = load_dataset(
            cast(str, _guess_by_ext(train_dataset.path)),
            data_files=train_dataset.path,
        )["train"]
    else:
        logger.info("loading training dataset from remote...")
        trainset = load_dataset(
            path=train_dataset.path,
            name=train_dataset.name,
            split=train_dataset.split,
        )
    logger.info("training dataset loaded")

    dspy_trainset = [dspy.Example(inp=x).with_inputs("inp") for x in trainset]

    module = _WorkflowWrapperModule(workflow, init_system_prompt)

    # teacher lm
    lm = dspy.LM(config.lm_model_name)

    optimizer = dspy.MIPROv2(
        metric=(
            lambda data, output, trace=None: _wrap_judge_fn(judge_func)(
                data.inp,
                output,
            )
        ),
        auto=config.optimization_level,
        teacher_settings={
            "lm": lm,
        },
        prompt_model=lm,
        task_model=lm,
    )

    # optimize
    logger.info("optimizing workflow...")
    result = optimizer.compile(module, trainset=dspy_trainset)
    logger.info("workflow optimized")

    # evaluate if eval_dataset is provided
    valset_improvement: float | None = None
    if eval_dataset is not None:
        if os.path.exists(eval_dataset.path) and _guess_by_ext(
            eval_dataset.path,
        ):
            logger.info(
                "loading evaluation dataset from file: %s",
                eval_dataset.path,
            )
            evalset = load_dataset(
                cast(str, _guess_by_ext(eval_dataset.path)),
                data_files=eval_dataset.path,
            )["train"]
        else:
            logger.info("loading evaluation dataset from remote...")
            evalset = load_dataset(
                path=eval_dataset.path,
                name=eval_dataset.name,
                split=eval_dataset.split,
            )
        logger.info("evaluation dataset loaded")

        dspy_evalset = [
            dspy.Example(inp=x).with_inputs("inp") for x in evalset
        ]

        evaluate = dspy.Evaluate(
            devset=dspy_evalset,
            metric=lambda data, output, trace=None: _wrap_judge_fn(
                judge_func,
            )(data.inp, output),
            display_progress=config.eval_display_progress,
            display_table=config.eval_display_table,
            num_threads=config.eval_num_threads,
        )

        baseline_score = None
        if config.compare_performance:
            logger.info("evaluating baseline performance...")
            baseline_res = evaluate(module)
            baseline_score = baseline_res.score
            logger.info("baseline score: %s", baseline_score)

        logger.info("evaluating optimized results...")
        eval_res = evaluate(result)
        score = eval_res.score
        logger.info("optimized score: %s", score)

        if baseline_score is not None:
            valset_improvement = (
                (score - baseline_score) / baseline_score * 100
                if baseline_score != 0
                else 0.0
            )
            logger.info("improvement: %.2f%%", valset_improvement)

    optimized_prompt = result.predictor.get_current_prompt()
    assert isinstance(optimized_prompt, str), f"Optimized prompt must be a string but {type(optimized_prompt)}. This should not happen."
    logger.info("---------- Optimized Prompt ----------")
    logger.info(optimized_prompt)
    logger.info("--------------------------------------")

    metrics: dict[str, float] = {}
    if valset_improvement is not None:
        metrics["valset_improvement"] = valset_improvement

    return optimized_prompt, metrics
