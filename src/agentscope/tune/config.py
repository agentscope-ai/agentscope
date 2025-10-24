# -*- coding: utf-8 -*-
"""The configurations for agent learning."""

from dataclasses import dataclass

from .workflow import (
    WorkflowType,
)


try:
    from omegaconf import OmegaConf
    from trinity.common.config import Config

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

except ImportError:

    class TuneConfig:  # type: ignore [no-redef]
        """A placeholder class for TuneConfig when
        dependencies are missing."""

        def __init__(self) -> None:
            """Raise ImportError when instantiated."""
            raise ImportError(
                "Trinity-RFT or OmegaConf is not installed. "
                "Please install them with "
                "`pip install trinity-rft omegaconf`.",
            )

        @classmethod
        def load_config(cls, config_path: str) -> "TuneConfig":
            """Raise ImportError when trying to load configuration."""
            raise ImportError(
                "Trinity-RFT is not installed. Please "
                "install it with `pip install trinity-rft`.",
            )
