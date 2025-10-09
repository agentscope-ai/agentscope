# -*- coding: utf-8 -*-
from __future__ import annotations

"""The model manager for AgentScope."""
import importlib
import json
import os
from typing import Any, Union, Type, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ..model import ModelWrapperBase


class ModelManager:
    """The model manager for AgentScope, which is responsible for loading and
    managing model configurations and model."""

    _instance = None

    model_configs: dict[str, dict] = {}
    """The model configs"""

    model_wrapper_mapping: dict[str, Type["ModelWrapperBase"]] = {}
    """The registered model wrapper classes."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        """Create a singleton instance."""
        if cls._instance is None:
            cls._instance = super(ModelManager, cls).__new__(cls)
        else:
            raise RuntimeError(
                "The Model manager has been initialized. Try to use "
                "ModelManager.get_instance() to get the instance.",
            )
        return cls._instance

    @classmethod
    def get_instance(cls) -> "ModelManager":
        """Get the instance of the singleton class."""
        if cls._instance is None:
            raise ValueError(
                "AgentScope hasn't been initialized. Please call "
                "`agentscope.init` function first.",
            )
        return cls._instance

    def __init__(
        self,
    ) -> None:
        """Initialize the model manager with model configs"""
        from ..model import _BUILD_IN_MODEL_WRAPPERS

        self.model_configs = {}
        self.model_wrapper_mapping = {}

        for cls_name in _BUILD_IN_MODEL_WRAPPERS:
            models_module = importlib.import_module("agentscope.model")
            cls = getattr(models_module, cls_name)
            if getattr(cls, "model_type", None):
                self.register_model_wrapper_class(cls, exist_ok=False)

    def initialize(
        self,
        model_configs: Union[dict, str, list, None] = None,
    ) -> None:
        """Initialize the model manager with model configs."""
        if model_configs is not None:
            self.load_model_configs(model_configs)

    def clear_model_configs(self) -> None:
        """Clear the loaded model configs."""
        self.model_configs.clear()

    def load_model_configs(
        self,
        model_configs: Union[dict, str, list],
        clear_existing: bool = False,
    ) -> None:
        """read model configs from a path or a list of dicts.

        Args:
            model_configs (`Union[str, list, dict]`):
                The path of the model configs | a config dict | a list of model
                configs.
            clear_existing (`bool`, defaults to `False`):
                Whether to clear the loaded model configs before reading.

        Returns:
            `dict`:
                The model configs.
        """
        if clear_existing:
            self.clear_model_configs()

        cfgs = model_configs

        # Load model configs from a path
        if isinstance(cfgs, str):
            if not os.path.exists(cfgs):
                raise FileNotFoundError(
                    f"Cannot find the model configs file in the given path "
                    f"`{model_configs}`.",
                )
            with open(cfgs, "r", encoding="utf-8") as f:
                cfgs = json.load(f)

        # Load model configs from a dict or a list of dicts
        if isinstance(cfgs, dict):
            cfgs = [cfgs]

        if isinstance(cfgs, list):
            if not all(isinstance(_, dict) for _ in cfgs):
                raise ValueError(
                    "The model config unit should be a dict.",
                )
        else:
            raise TypeError(
                f"Invalid type of model_configs, it could be a dict, a list "
                f"of dicts, or a path to a json file (containing a dict or a "
                f"list of dicts), but got {type(model_configs)}",
            )

        # Check and register the model configs
        for cfg in cfgs:
            # Check the format of model configs
            if "config_name" not in cfg or "model_type" not in cfg:
                raise ValueError(
                    "The `config_name` and `model_type` fields are required "
                    f"for model config, but got: {cfg}",
                )

            config_name = cfg["config_name"]
            if config_name in self.model_configs:
                logger.warning(
                    f"Config name [{config_name}] already exists.",
                )
                continue
            self.model_configs[config_name] = cfg

        # print the loaded model configs
        logger.info(
            "Load configs for model wrapper: {}",
            ", ".join(self.model_configs.keys()),
        )

    def get_model_by_config_name(self, config_name: str) -> ModelWrapperBase:
        """Load the model by config name, and return the model wrapper."""
        if len(self.model_configs) == 0:
            raise ValueError(
                "No model configs loaded, please call "
                "`read_model_configs` first.",
            )

        # Find model config by name
        if config_name not in self.model_configs:
            raise ValueError(
                f"Cannot find [{config_name}] in loaded configurations.",
            )
        config = self.model_configs.get(config_name, None)

        if config is None:
            raise ValueError(
                f"Cannot find [{config_name}] in loaded configurations.",
            )

        model_type = config["model_type"]
        if model_type not in self.model_wrapper_mapping:
            raise ValueError(
                f"Unsupported model_type `{model_type}`, currently supported "
                f"model types: "
                f"{', '.join(list(self.model_wrapper_mapping.keys()))}. ",
            )

        kwargs = {k: v for k, v in config.items() if k != "model_type"}

        return self.model_wrapper_mapping[model_type](**kwargs)

    def get_config_by_name(self, config_name: str) -> Union[dict, None]:
        """Load the model config by name, and return the config dict."""
        return self.model_configs.get(config_name, None)

    def state_dict(self) -> dict:
        """Serialize the model manager into a dict."""
        return {
            "model_configs": self.model_configs,
        }

    def load_dict(self, data: dict) -> None:
        """Load the model manager from a dict."""
        self.clear_model_configs()
        assert "model_configs" in data
        self.model_configs = data["model_configs"]

    def register_model_wrapper_class(
        self,
        model_wrapper_class: Type[ModelWrapperBase],
        exist_ok: bool,
    ) -> None:
        """Register the model wrapper class.

        Args:
            model_wrapper_class (`Type[ModelWrapperBase]`):
                The model wrapper class to be registered, which must inherit
                from `ModelWrapperBase`.
            exist_ok (`bool`):
                Whether to overwrite the existing model wrapper with the same
                name.
        """

        if not issubclass(model_wrapper_class, ModelWrapperBase):
            raise TypeError(
                "The model wrapper class should inherit from "
                f"ModelWrapperBase, but got {model_wrapper_class}.",
            )

        if not hasattr(model_wrapper_class, "model_type"):
            raise ValueError(
                f"The model wrapper class `{model_wrapper_class}` should "
                f"have a `model_type` attribute.",
            )

        model_type = model_wrapper_class.model_type
        if model_type in self.model_wrapper_mapping:
            if exist_ok:
                logger.warning(
                    f'Model wrapper "{model_type}" '
                    "already exists, overwrite it.",
                )
                self.model_wrapper_mapping[model_type] = model_wrapper_class
            else:
                raise ValueError(
                    f'Model wrapper "{model_type}" already exists, '
                    "please set `exist_ok=True` to overwrite it.",
                )
        else:
            self.model_wrapper_mapping[model_type] = model_wrapper_class

    def flush(self) -> None:
        """Flush the model manager."""
        self.clear_model_configs()
