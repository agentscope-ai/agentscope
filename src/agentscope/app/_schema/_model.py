# -*- coding: utf-8 -*-
"""The chat model configuration, used as DTO layer."""

from pydantic import BaseModel, Field

from ...model import ModelCard


class ListModelResponse(BaseModel):
    """List the candidate models response."""

    models: list[ModelCard] = Field(description="The candidate models.")
    total: int = Field(description="The total number of candidates.")


class ListModelRequest(BaseModel):
    """List the candidate models request."""

    provider: str = Field(
        description="The provider type, e.g. openai, dashscope, etc.",
    )
