"""Prompt tuning functionality using DSPy's MIPROv2 optimizer."""

import os
from pathlib import Path
from agentscope.tuner import (
    DatasetConfig,
    TunerModelConfig,
)
import asyncio
from typing import Any, Callable, Optional, cast

from datasets import load_dataset
import dspy
from agentscope import logger
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._config import check_judge_function
from agentscope.tuner._workflow import WorkflowType
from agentscope.tuner._judge import JudgeType
from agentscope.tuner.prompt_tune.config import PromptTuneConfig
from agentscope.tuner.prompt_tune.wrapper import WorkflowWrapperModule


def wrap_judge_fn(judge_fn: JudgeType):
    """Wrap an async judge function into a synchronous callable.

    Args:
        judge_fn: The async judge function to wrap.

    Returns:
        A synchronous wrapper function that returns only the reward value.
    """
    async def inner(task: dict, response: Any, auxiliary_models: dict[str, ChatModelBase]):
        output = await judge_fn(task, response, auxiliary_models)
        return output.reward

    def _sync_wrapper(task: dict, response: Any, auxiliary_models: dict[str, ChatModelBase]):
        return asyncio.run(inner(task, response, auxiliary_models))

    return _sync_wrapper


def _guess_by_ext(p: str) -> Optional[str]:
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

def tune_prompt(
    *,
    workflow_func: Callable[[str], WorkflowType],
    init_system_prompt: str,
    judge_func: JudgeType,
    train_dataset: DatasetConfig,
    eval_dataset: DatasetConfig | None = None,
    model: ChatModelBase,
    auxiliary_models: dict[str, ChatModelBase] | None = None,
    config: PromptTuneConfig | None = None,
) -> str:
    """Tune a system prompt using DSPy's MIPROv2 optimizer.

    This function optimizes the system prompt by leveraging DSPy's
    automatic prompt optimization capabilities.

    Args:
        workflow_func: A factory function that takes a system prompt (str) and
            returns an async workflow function.
        init_system_prompt: The initial system prompt to be optimized.
        judge_func: An async function that evaluates the agent's response and
            returns a JudgeOutput.
        train_dataset: The dataset used for training/optimization.
        eval_dataset: Optional dataset for evaluation after optimization.
        model: The chat model used in the workflow.
        auxiliary_models: Optional dictionary of additional chat models for
            LLM-as-a-Judge usage.
        config: Configuration for prompt tuning. Defaults to PromptTuneConfig().

    Returns:
        The optimized system prompt string.
    """
    config = config or PromptTuneConfig()
    auxiliary_models = auxiliary_models or {}
    logger.warning("Model will not be optimized during prompt tuning.")
    check_judge_function(judge_func)

    if os.path.exists(train_dataset.path) and _guess_by_ext(train_dataset.path):
        logger.info(f"loading dataset from file: {train_dataset.path}")
        trainset = load_dataset(
            cast(str, _guess_by_ext(train_dataset.path)),
            data_files=train_dataset.path,
        )['train']
    else:
        logger.info("loading training dataset from remote...")
        trainset = load_dataset(
            path=train_dataset.path,
            name=train_dataset.name,
            split=train_dataset.split,
        )
    logger.info("training dataset loaded")

    dspy_trainset = [
        dspy.Example(inp=x).with_inputs("inp") for x in trainset
    ]

    module = WorkflowWrapperModule(workflow_func, init_system_prompt)
    # model and auxiliary_models are not necessary for prompt tuning.
    # what about providing a new interface?
    module._set_chatmodel(model, auxiliary_models)

    # teacher lm
    lm = dspy.LM(config.lm_model_name)

    optimizer = dspy.MIPROv2(
        metric=(
            lambda data, output, trace=None: wrap_judge_fn(judge_func)(
                data.inp,
                output,
                auxiliary_models,
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
    if eval_dataset is not None:
        if os.path.exists(eval_dataset.path) and _guess_by_ext(eval_dataset.path):
            logger.info(f"loading evaluation dataset from file: {eval_dataset.path}")
            evalset = load_dataset(
                cast(str, _guess_by_ext(eval_dataset.path)),
                data_files=eval_dataset.path,
            )['train']
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
            metric=lambda data, output, trace=None: wrap_judge_fn(
                judge_func,
            )(data.inp, output, auxiliary_models),
            display_progress=config.eval_display_progress,
            display_table=config.eval_display_table,
            num_threads=config.eval_num_threads,
        )

        baseline_score = None
        if config.compare_performance:
            logger.info("evaluating baseline performance...")
            baseline_res = evaluate(module)
            baseline_score = baseline_res.score
            logger.info(f"baseline score: {baseline_score}")

        logger.info("evaluating optimized results...")
        eval_res = evaluate(result)
        score = eval_res.score
        logger.info(f"optimized score: {score}")

        if baseline_score is not None:
            improvement = (
                (score - baseline_score) / baseline_score * 100
                if baseline_score != 0
                else 0
            )
            logger.info(f"improvement: {improvement:.2f}%")

    optimized_prompt = result.predictor.get_current_prompt()
    logger.info("---------- Optimized Prompt ----------")
    logger.info(optimized_prompt)
    logger.info("--------------------------------------")

    return optimized_prompt
