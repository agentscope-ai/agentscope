# -*- coding: utf-8 -*-
""""""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class ModelStatus(str, Enum):
    """The model status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUNSET = "sunset"


class ModelSchema(BaseModel):
    """The model schema, used for frontend to

    - get the candidate models,
    - render the model configuration form, and
    - validate the model configuration.
    """

    # Basic information
    name: str
    label: str

    # Lifecycle
    status: ModelStatus
    deprecated_at: datetime | None = None
    sunset_at: datetime | None = None
    replacement: str | None = None

    # Capabilities
    input: list[Literal["text", "image", "audio"]]
    output: list[Literal["text", "audio", "thinking", "tool_call"]]

    # Context length
    context_length: int | None = None

    # Parameters that valid to this model, which will be extract from the
    # corresponding Parameter class, e.g. DashScopeLLMParameter
    parameters: list[str] = ["stream", "temperature", "topK"]


