# -*- coding: utf-8 -*-
"""The realtime model card."""
import copy
from datetime import datetime
from pathlib import Path
from typing import Literal, Self, Type

import yaml
from pydantic import BaseModel, Field

from .._logging import logger


class RealtimeModelCard(BaseModel):
    """Describes one realtime model. Everything here varies with the model
    name; what the adapter implements lives on
    :class:`RealtimeModelBase`."""

    type: Literal["realtime_model"] = "realtime_model"

    model_type: str = Field(
        default="",
        description="The adapter class this model belongs to, e.g. "
        "``dashscope_omni_realtime``.",
    )

    name: str = Field(description="The model identifier.")
    label: str = Field(description="Human-readable label for the UI.")

    status: Literal["active", "deprecated", "sunset"] = Field(
        default="active",
        description="Lifecycle status of the model.",
    )
    deprecated_at: datetime | None = Field(
        default=None,
        description="When the model was deprecated, if applicable.",
    )

    input_types: list[str] = Field(
        default_factory=lambda: ["audio/pcm"],
        description="Accepted input media types.",
    )
    output_types: list[str] = Field(
        default_factory=lambda: ["audio/pcm", "text/plain"],
        description="Media types the model produces.",
    )

    input_sample_rate: int = Field(
        default=16000,
        gt=0,
        description="Advertised input PCM sample rate in Hz.",
    )
    output_sample_rate: int = Field(
        default=24000,
        gt=0,
        description="Advertised output PCM sample rate in Hz.",
    )

    supports_tools: bool = Field(
        default=False,
        description="Whether this model accepts tool schemas.",
    )
    supports_session_resumption: bool = Field(
        default=False,
        description="Whether a previous session can be resumed by handle.",
    )

    # Context limits. Providers use different units and several can apply
    # at once, so all are optional; ``None`` means unlimited or unknown.
    max_context_tokens: int | None = Field(default=None, gt=0)
    max_audio_turns: int | None = Field(default=None, gt=0)
    max_audio_duration_s: int | None = Field(default=None, gt=0)
    max_session_duration_s: int | None = Field(default=None, gt=0)

    parameter_schema: dict = Field(default_factory=dict)
    """JSON Schema of the tuneable parameters, surfaced to the UI."""

    parameter_overrides: dict[str, dict] = Field(default_factory=dict)
    """Per-parameter narrowing merged into :attr:`parameter_schema`."""

    @classmethod
    def from_yaml(
        cls,
        yaml_path: str | Path,
        parameter_class: Type[BaseModel],
    ) -> Self:
        """Load a card from YAML, seeding the parameter schema from
        *parameter_class*.

        Args:
            yaml_path (`str | Path`):
                Path to the YAML file.
            parameter_class (`Type[BaseModel]`):
                The adapter's ``Parameters`` class, whose JSON schema seeds
                :attr:`parameter_schema`.

        Returns:
            `RealtimeModelCard`: The loaded card.
        """
        with open(yaml_path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)

        properties = copy.deepcopy(
            parameter_class.model_json_schema().get("properties", {}),
        )
        overrides = config.pop("parameter_overrides", {}) or {}
        for param, override in overrides.items():
            if override is None or override.get("hidden"):
                properties.pop(param, None)
            elif param in properties:
                properties[param] = {**properties[param], **override}

        return cls(
            **config,
            parameter_schema={"type": "object", "properties": properties},
            parameter_overrides=overrides,
        )

    @classmethod
    def list_from_directory(
        cls,
        yaml_dir: str | Path,
        parameter_class: Type[BaseModel],
    ) -> list["RealtimeModelCard"]:
        """Load every ``*.yaml`` card in *yaml_dir*, skipping broken ones."""
        cards: list["RealtimeModelCard"] = []
        for yaml_file in sorted(Path(yaml_dir).glob("*.yaml")):
            try:
                cards.append(cls.from_yaml(yaml_file, parameter_class))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to load realtime model card %s: %s",
                    yaml_file,
                    exc,
                )
        return cards
