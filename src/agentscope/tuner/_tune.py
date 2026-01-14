# -*- coding: utf-8 -*-
"""The main entry point for agent learning."""
import os
from ._workflow import WorkflowType
from ._judge import JudgeType
from ._model import TunerModelConfig
from ._dataset import DatasetConfig
from ._config import (
    _to_trinity_config,
    check_judge_function,
    check_workflow_function,
)
from ._algorithm import AlgorithmConfig


def tune(
    *,
    workflow_func: WorkflowType,
    judge_func: JudgeType | None = None,
    train_dataset: DatasetConfig | None = None,
    eval_dataset: DatasetConfig | None = None,
    model: TunerModelConfig | None = None,
    auxiliary_models: dict[str, TunerModelConfig] | None = None,
    algorithm: AlgorithmConfig | None = None,
    project_name: str | None = None,
    experiment_name: str | None = None,
    monitor_type: str | None = None,
    config_path: str | None = None,
) -> None:
    """Train the agent workflow with the specific configuration.

    Args:
        workflow_func (`WorkflowType`): The learning workflow function
            to execute.
        judge_func (`JudgeType`, optional): The judge function used to
            evaluate the workflow output. Defaults to None.
        train_dataset (`DatasetConfig`, optional): The training dataset for
            the learning process. Defaults to None.
        eval_dataset (`DatasetConfig`, optional): The evaluation dataset for
            the learning process. Defaults to None.
        model (`TunerModelConfig`, optional): The model to be tuned.
            Defaults to None.
        auxiliary_models (`dict[str, TunerModelConfig]`, optional): A
            dictionary of auxiliary models for LLM-as-a-Judge
            or acting other agents in multi-agent scenarios.
            Defaults to None.
        algorithm (`AlgorithmConfig`, optional): The tuning algorithm
            configuration. Defaults to None.
        project_name (`str`, optional): Name of the project.
            Defaults to None.
        experiment_name (`str`, optional): Name of the experiment.
            Leave None to use timestamp. Defaults to None.
        monitor_type (`str`, optional): Type of the monitor to use.
            Could be one of 'tensorboard', 'wandb', 'mlflow', 'swanlab'.
            Leave None to use tensorboard. Defaults to None.
        config_path (`str`, optional): Path to a trinity yaml configuration
            file. If provided, only `workflow_func` is necessary, other
            arguments will override the corresponding fields in the config.
            Defaults to None.
    """
    try:
        from trinity.cli.launcher import run_stage
        from trinity.utils.dlc_utils import setup_ray_cluster, stop_ray_cluster
    except ImportError as e:
        raise ImportError(
            "Trinity-RFT is not installed. Please install it with "
            "`pip install trinity-rft`.",
        ) from e

    check_workflow_function(workflow_func)
    if judge_func is not None:
        check_judge_function(judge_func)

    config = _to_trinity_config(
        config_path=config_path,
        workflow_func=workflow_func,
        judge_func=judge_func,
        model=model,
        auxiliary_models=auxiliary_models,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        algorithm=algorithm,
        project_name=project_name,
        experiment_name=experiment_name,
        monitor_type=monitor_type,
    )
    use_dlc = os.environ.get("USE_ALIYUN_PAI_DLC", "0") == "1"
    if use_dlc:
        config.cluster.ray_address = setup_ray_cluster(namespace="agentscope")
    try:
        return run_stage(
            config=config.check_and_update(),
        )
    finally:
        if use_dlc:
            stop_ray_cluster(namespace="agentscope")
