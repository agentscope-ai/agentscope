# -*- coding: utf-8 -*-
"""Model metadata for speech recognition implementations."""

from typing import Literal, Self, Type

from pydantic import BaseModel, Field
import yaml


class ASRModelCard(BaseModel):
    """A model and its supported audio input types."""

    type: Literal["asr_model"] = "asr_model"
    name: str
    label: str
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=lambda: ["text/plain"])
    parameter_schema: dict

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str,
        parameter_class: Type[BaseModel],
    ) -> Self:
        """Load a card and the implementation's parameter schema."""
        with open(yaml_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)
        return cls(
            name=config["name"],
            label=config["label"],
            input_types=config["input_types"],
            output_types=config.get("output_types", ["text/plain"]),
            parameter_schema=parameter_class.model_json_schema(),
        )
