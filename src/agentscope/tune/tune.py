# -*- coding: utf-8 -*-
"""The main entry point for agent learning."""
from .workflow import (
    WorkflowType,
    validate_function_signature,
)
from .config import TuneConfig


def tune(workflow_func: WorkflowType, config: TuneConfig) -> None:
    """Train the agent workflow with the specific configuration.

    Args:
        workflow_func (WorkflowType): The learning workflow function
            to execute.
        config (TuneConfig): The configuration for the learning process.
    """
    try:
        from trinity.cli.launcher import run_stage
    except ImportError as e:
        raise ImportError(
            "Trinity-RFT is not installed. Please install it with "
            "`pip install trinity-rft`.",
        ) from e

    if not validate_function_signature(workflow_func):
        raise ValueError(
            "Invalid workflow function signature, please "
            "check the types of your workflow input/output.",
        )
    return run_stage(
        config.to_trinity_config(workflow_func=workflow_func),
    )
