# -*- coding: utf-8 -*-
"""The main entry point for agent learning."""
from dataclasses import dataclass
from ._workflow import (
    WorkflowType,
    _validate_function_signature,
)


def tune(workflow_func: WorkflowType, config_path: str) -> None:
    """Train the agent workflow with the specific configuration.

    Args:
        workflow_func (WorkflowType): The learning workflow function
            to execute.
        config_path (str): The configuration for the learning process.
    """
    try:
        from trinity.cli.launcher import run_stage
        from trinity.common.config import Config
        from omegaconf import OmegaConf
    except ImportError as e:
        raise ImportError(
            "Trinity-RFT is not installed. Please install it with "
            "`pip install trinity-rft`.",
        ) from e

    if not _validate_function_signature(workflow_func):
        raise ValueError(
            "Invalid workflow function signature, please "
            "check the types of your workflow input/output.",
        )

    @dataclass
    class TuneConfig(Config):
        """Configuration for learning process."""

        def to_trinity_config(self, workflow_func: WorkflowType) -> Config:
            """Convert to Trinity-RFT compatible configuration."""
            workflow_name = "agentscope_workflow_adapter"
            self.buffer.explorer_input.taskset.default_workflow_type = (
                workflow_name
            )
            self.buffer.explorer_input.default_workflow_type = workflow_name
            self.buffer.explorer_input.taskset.workflow_args[
                "workflow_func"
            ] = workflow_func
            return self.check_and_update()

        @classmethod
        def load_config(cls, config_path: str) -> "TuneConfig":
            """Load the learning configuration from a YAML file.

            Args:
                config_path (str): The path to the configuration file.

            Returns:
                TuneConfig: The loaded learning configuration.
            """
            schema = OmegaConf.structured(cls)
            yaml_config = OmegaConf.load(config_path)
            try:
                config = OmegaConf.merge(schema, yaml_config)
                return OmegaConf.to_object(config)
            except Exception as e:
                raise ValueError(f"Invalid configuration: {e}") from e

    return run_stage(
        config=TuneConfig.load_config(config_path).to_trinity_config(
            workflow_func,
        ),
    )
