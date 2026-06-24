# -*- coding: utf-8 -*-
"""The realtime model card class."""
import copy
from datetime import datetime
from pathlib import Path
from typing import Literal, Self, Type

import yaml
from pydantic import BaseModel, Field


class RealtimeModelCard(BaseModel):
    """Model card for realtime (bidirectional streaming) models."""

    type: Literal["realtime_model"] = "realtime_model"
    """The model card type."""

    name: str = Field(description="The model identifier.")
    """The model name (e.g. ``'qwen3-omni-flash-realtime'``)."""

    label: str = Field(description="Human-readable label for the UI.")
    """Display label for the frontend."""

    status: Literal["active", "deprecated", "sunset"] = Field(
        default="active",
        description="Lifecycle status of the model.",
    )
    """Model availability status."""

    deprecated_at: datetime | None = Field(
        default=None,
        description="When the model was deprecated, if applicable.",
    )
    """Deprecation timestamp, if any."""

    input_types: list[str] = Field(
        default_factory=lambda: ["audio/pcm", "text/plain"],
        description="Accepted input media types.",
    )
    """Input modalities (e.g. ``['audio/pcm', 'text/plain']``)."""

    output_types: list[str] = Field(
        default_factory=lambda: ["audio/pcm", "text/plain"],
        description="Output media types produced by the model.",
    )
    """Output modalities."""

    context_size: int = Field(
        default=0,
        ge=0,
        description="Maximum context window in tokens.",
    )
    """Max context window size (tokens).  0 means unspecified."""

    output_size: int = Field(
        default=0,
        ge=0,
        description="Maximum output tokens per response.",
    )
    """Max output tokens per response.  0 means unspecified."""

    parameter_schema: dict = Field(default_factory=dict)
    """JSON Schema describing tuneable parameters exposed to the UI."""

    parameters_overrides: dict[str, dict] = Field(default_factory=dict)
    """Per-parameter overrides merged into ``parameter_schema``."""

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str,
        parameter_class: Type[BaseModel],
    ) -> Self:
        """Load a realtime model card from a YAML file.

        Args:
            yaml_path: Path to the YAML file.
            parameter_class: Pydantic model whose JSON schema seeds
                ``parameter_schema``.

        Returns:
            A ``RealtimeModelCard`` instance.
        """
        with open(yaml_path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)

        base_schema = parameter_class.model_json_schema()
        properties = copy.deepcopy(base_schema.get("properties", {}))

        overrides = config.get("parameter_overrides", {})
        for param_name, override in overrides.items():
            if override is None:
                properties.pop(param_name, None)
                continue
            if isinstance(override, dict):
                if override.get("hidden"):
                    properties.pop(param_name, None)
                    continue
                if param_name in properties:
                    properties[param_name] = {
                        **properties[param_name],
                        **override,
                    }

        final_schema = {
            "type": "object",
            "properties": properties,
            "required": base_schema.get("required", []),
        }

        return cls(
            name=config["name"],
            label=config["label"],
            status=config.get("status", "active"),
            deprecated_at=config.get("deprecated_at"),
            input_types=config.get("input_types", ["audio/pcm", "text/plain"]),
            output_types=config.get(
                "output_types",
                ["audio/pcm", "text/plain"],
            ),
            context_size=config.get("context_size", 0),
            output_size=config.get("output_size", 0),
            parameter_schema=final_schema,
            parameters_overrides=config.get("parameter_overrides", {}),
        )

    @classmethod
    def list_from_directory(
        cls,
        yaml_dir: str | Path,
        parameter_class: Type[BaseModel],
    ) -> list["RealtimeModelCard"]:
        """Scan a directory for ``*.yaml`` files and return model cards.

        Args:
            yaml_dir: Directory containing YAML model card files.
            parameter_class: Forwarded to :meth:`from_yaml`.

        Returns:
            List of successfully loaded model cards.
        """
        from .._logging import logger

        yaml_dir = Path(yaml_dir)
        cards: list[RealtimeModelCard] = []
        for yaml_file in sorted(yaml_dir.glob("*.yaml")):
            try:
                cards.append(cls.from_yaml(str(yaml_file), parameter_class))
            except Exception as exc:
                logger.warning(
                    "Failed to load realtime model card %s: %s",
                    yaml_file,
                    exc,
                )
        return cards
