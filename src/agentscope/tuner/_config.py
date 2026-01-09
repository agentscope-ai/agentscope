# -*- coding: utf-8 -*-
"""Configuration conversion for tuner."""
from typing import Any, Callable, List
from pathlib import Path
from datetime import datetime
import inspect

from ._workflow import WorkflowType
from ._judge import JudgeType
from ..model import TunerChatModel
from ._dataset import Dataset
from ._algorithm import Algorithm


def _set_if_not_none(obj: Any, field: str, value: Any) -> None:
    """Set the field of obj to value if value is not None."""
    if value is not None:
        setattr(obj, field, value)


def _to_trinity_config(
    *,
    config_path: str | None = None,
    workflow_func: WorkflowType | None = None,
    judge_func: JudgeType | None = None,
    model: TunerChatModel | None = None,
    auxiliary_models: dict[str, TunerChatModel] | None = None,
    train_dataset: Dataset | None = None,
    eval_dataset: Dataset | None = None,
    algorithm: Algorithm | None = None,
    project_name: str | None = None,
    experiment_name: str | None = None,
    monitor_type: str | None = None,
) -> Any:
    """Convert to Trinity-RFT compatible configuration."""
    from trinity.common.config import (
        Config,
        TasksetConfig,
        load_config,
        InferenceModelConfig,
    )

    auto_config = False
    if config_path is None:
        temp_path = Path(__file__).parent / "template" / "config.yaml"
        config_path = str(temp_path.absolute())
        auto_config = True

    config = load_config(config_path)
    assert isinstance(config, Config), "Loaded config is not valid."

    _set_if_not_none(config, "project", project_name)
    if experiment_name is None and auto_config:
        config.name = "Experiment-" + datetime.now().strftime(
            "%Y%m%d%H%M%S",
        )

    _set_if_not_none(config, "monitor", monitor_type)

    workflow_name = "agentscope_workflow_adapter_v1"
    if train_dataset is not None:
        if config.buffer.explorer_input.taskset is None:
            config.buffer.explorer_input.taskset = TasksetConfig(
                name="train_taskset",
                path=train_dataset.path,
                split=train_dataset.split,
                subset_name=train_dataset.name,
            )
        else:
            config.buffer.explorer_input.taskset.path = train_dataset.path
            config.buffer.explorer_input.taskset.split = train_dataset.split
            config.buffer.explorer_input.taskset.subset_name = (
                train_dataset.name
            )
        config.buffer.total_epochs = train_dataset.total_epochs
        config.buffer.total_steps = train_dataset.total_steps
    config.buffer.explorer_input.taskset.default_workflow_type = workflow_name
    config.buffer.explorer_input.default_workflow_type = workflow_name
    workflow_args = {
        "workflow_func": workflow_func,
    }
    if judge_func is not None:
        workflow_args["judge_func"] = judge_func

    config.buffer.explorer_input.taskset.workflow_args = workflow_args

    if model is not None:
        model_config = model.get_config()
        config.model.model_path = model_config["model_path"]
        config.model.max_model_len = model_config["max_model_len"]
        config.model.max_response_tokens = model.max_tokens
        config.explorer.rollout_model = InferenceModelConfig(
            **model.get_config(),
        )
        config.explorer.rollout_model.enable_history = True
    if auxiliary_models is not None:
        for name, aux_chat_model in auxiliary_models.items():
            model_config = InferenceModelConfig(
                **aux_chat_model.get_config(),
            )
            model_config.name = name
            config.explorer.auxiliary_models.append(
                model_config,
            )
    if eval_dataset is not None:
        config.buffer.explorer_input.eval_tasksets.append(
            TasksetConfig(
                name="eval_taskset",
                path=eval_dataset.path,
                split=eval_dataset.split,
                subset_name=eval_dataset.name,
                workflow_args=workflow_args,
            ),
        )
    if algorithm is not None:
        config.algorithm.algorithm_type = algorithm.algorithm_type
        config.algorithm.repeat_times = algorithm.group_size
        config.algorithm.optimizer.lr = algorithm.learning_rate
        config.buffer.batch_size = algorithm.batch_size
        config.trainer.save_interval = algorithm.save_interval_steps
        config.explorer.eval_interval = algorithm.eval_interval_steps
    return config


def check_workflow_function(
    func: Callable,
) -> None:
    """Check if the given function is a valid WorkflowType.

    Args:
        func (Callable): The function to check.
    """
    essential_params = ["task", "model"]
    optional_params = ["auxiliary_models"]
    _check_function_signature(
        func,
        essential_params,
        optional_params,
    )


def check_judge_function(
    func: Callable,
) -> None:
    """Check if the given function is a valid JudgeType.

    Args:
        func (Callable): The function to check.
    """
    essential_params = ["task", "response"]
    optional_params = ["auxiliary_models"]
    _check_function_signature(
        func,
        essential_params,
        optional_params,
    )


def _check_function_signature(
    func: Callable,
    essential_params: List[str],
    optional_params: List[str] | None = None,
) -> None:
    """
    Check if the given function has the required signature.

    Args:
        func (`Callable`): The function to check.
        essential_params (`List[str]`): List of essential parameter names
            that must be present in the function.
        optional_params (`List[str] | None`): List of optional parameter names
            that can be present in the function.
    """
    if optional_params is None:
        optional_params = []

    sig = inspect.signature(func)
    actual_params = []

    for param_name, param in sig.parameters.items():
        # *args and **kwargs are not allowed
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            raise ValueError(f"*args parameter is not allowed: *{param_name}")
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            raise ValueError(
                f"**kwargs parameter is not allowed: **{param_name}",
            )
        actual_params.append(param_name)

    # Convert to sets for easier comparison
    actual_params_set = set(actual_params)
    essential_params_set = set(essential_params)
    optional_params_set = set(optional_params)
    allowed_params_set = essential_params_set | optional_params_set

    # Check 1: All essential parameters are present
    missing_essential = essential_params_set - actual_params_set
    if missing_essential:
        raise ValueError(
            f"Missing essential parameters: {sorted(missing_essential)}",
        )

    # Check 2: Whether there are disallowed parameters
    extra_params = actual_params_set - allowed_params_set
    if extra_params:
        raise ValueError(
            f"Contains disallowed parameters: {sorted(extra_params)}",
        )
