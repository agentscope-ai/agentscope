# -*- coding: utf-8 -*-
"""Model metadata for speech recognition implementations."""

import copy
from typing import Literal, Self, Type

from pydantic import BaseModel, Field
import yaml


class ASRModelCard(BaseModel):
    """A model and its supported audio input types."""

    type: Literal["asr_model"] = "asr_model"
    """The model card type discriminator."""

    name: str = Field(
        description="The model identifier used in provider API calls.",
    )
    """The model identifier used in provider API calls."""

    label: str = Field(description="The human-readable model label.")
    """The human-readable model label."""

    input_types: list[str] = Field(
        default_factory=list,
        description="The supported audio input media types.",
    )
    """The supported audio input media types."""

    output_types: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        description="The supported transcription output media types.",
    )
    """The supported transcription output media types."""

    realtime: bool = Field(
        default=False,
        description="Whether the model supports realtime streaming input.",
    )
    """Whether the model supports realtime streaming input."""

    parameter_schema: dict = Field(
        description="The JSON schema for provider-specific parameters.",
    )
    """The JSON schema for provider-specific parameters."""

    parameters_overrides: dict[str, dict] = Field(
        default_factory=dict,
        description="Per-model overrides applied to the parameter schema.",
    )
    """The YAML overrides used to tailor frontend-visible parameters."""

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str,
        parameter_class: Type[BaseModel],
    ) -> Self:
        """Load a model card and its frontend-visible parameter schema.

        Args:
            yaml_path (`str`):
                The path to the model-card YAML file.
            parameter_class (`Type[BaseModel]`):
                The provider parameter model used to generate the base JSON
                schema.

        Returns:
            `ASRModelCard`:
                The validated model card with per-model parameter overrides
                applied.

        Raises:
            `OSError`:
                If the YAML file cannot be read.
            `yaml.YAMLError`:
                If the YAML content is malformed.
            `KeyError`:
                If a required model-card field is missing.
            `pydantic.ValidationError`:
                If the resulting model card is invalid.
        """
        with open(yaml_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        base_schema = parameter_class.model_json_schema()
        properties = copy.deepcopy(base_schema.get("properties", {}))
        overrides = config.get("parameter_overrides", {})
        for parameter_name, override in overrides.items():
            if override is None or override.get("hidden"):
                properties.pop(parameter_name, None)
            elif parameter_name in properties:
                properties[parameter_name] = {
                    **properties[parameter_name],
                    **override,
                }

        required = [
            name
            for name in base_schema.get("required", [])
            if name in properties
        ]
        return cls(
            name=config["name"],
            label=config["label"],
            input_types=config.get("input_types", []),
            output_types=config.get("output_types", ["text/plain"]),
            realtime=config.get("realtime", False),
            parameter_schema={
                "type": "object",
                "properties": properties,
                "required": required,
            },
            parameters_overrides=overrides,
        )
