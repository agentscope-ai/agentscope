# -*- coding: utf-8 -*-
"""The model card class."""
import copy
from datetime import datetime
from typing import Literal, Self, Type

import yaml
from pydantic import BaseModel, Field


class ModelCard(BaseModel):
    """The model card class."""

    type: Literal["chat_model"] = "chat_model"
    """The model card type."""

    name: str = Field(description="The name of the model")
    """The model name."""

    label: str = Field(description="The model label.")
    """The model label used for frontend rendering."""

    status: Literal["active", "deprecated", "sunset"] = Field(
        title="Status",
        description="The model status",
    )
    """The model status."""

    deprecated_at: datetime | None = Field(
        default=None,
        description="The model deprecation date and time.",
        title="Deprecation date",
    )
    """The model deprecated at."""

    input_types: list[str] = Field(
        description="The supported model input types.",
        title="Input types",
        default=["text/plain"],
    )
    """The model supported input types."""

    output_types: list[str] = Field(
        description="The supported model output types.",
        title="Output types",
        default=["text/plain"],
    )
    """The model supported output types."""

    context_size: int = Field(
        title="Context size",
        description="The context size.",
        gt=0,
    )
    """The model context size."""

    output_size: int = Field(
        title="Max output tokens",
        description="The maximum number of tokens.",
        gt=0,
    )
    """The model max output tokens."""

    parameter_schema: dict
    """The parameters schema, which will be combined with the schema from the
    DashScopeChatParameter class."""

    parameters_overrides: dict[str, dict]
    """The parameter overrides, which will be merged into the parameter schema.
    """

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str,
        parameter_class: Type[BaseModel],
    ) -> Self:
        """Read a model card from a YAML file, and merge the parameter schema
        with the override parameter schema in the yaml file.

        Args:
            yaml_path (`str`):
                Path to the YAML file
            parameter_class (`Type[BaseModel]`):
                The parameter class (e.g., DashScopeChatParameters)

        Returns:
            `list[ModelCard]`:
                ModelCard instance with merged parameter schema
        """

        # Load YAML config
        with open(yaml_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        # Get base schema from parameter class
        base_schema = parameter_class.model_json_schema()
        properties = copy.deepcopy(base_schema.get("properties", {}))

        # Auto-filter: remove thinking parameters if not supported
        output_types = config.get("output_types", [])
        if "application/x-thinking" not in output_types:
            properties.pop("thinking_enable", None)
            properties.pop("thinking_budget", None)

        # Auto-inject: set max_tokens maximum from output_size
        if "max_tokens" in properties and "output_size" in config:
            properties["max_tokens"]["maximum"] = config["output_size"]

        # Apply parameter_overrides with simple dict merge
        overrides = config.get("parameter_overrides", {})
        for param_name, override in overrides.items():
            if override is None:
                # null means remove
                properties.pop(param_name, None)
                continue

            if isinstance(override, dict):
                # Check for hidden flag
                if override.get("hidden"):
                    properties.pop(param_name, None)
                    continue

                # Simple dict merge: {**base, **override}
                if param_name in properties:
                    properties[param_name] = {
                        **properties[param_name],
                        **override,
                    }

        # Build final parameter schema
        final_schema = {
            "type": "object",
            "properties": properties,
            "required": base_schema.get("required", []),
        }

        # Create ModelCard instance
        return cls(
            name=config["name"],
            label=config["label"],
            status=config.get("status", "active"),
            deprecated_at=config.get("deprecated_at"),
            input_types=config.get("input_types", ["text/plain"]),
            output_types=config.get("output_types", ["text/plain"]),
            context_size=config["context_size"],
            output_size=config["output_size"],
            parameter_schema=final_schema,
            parameters_overrides=config.get("parameter_overrides", {}),
        )
