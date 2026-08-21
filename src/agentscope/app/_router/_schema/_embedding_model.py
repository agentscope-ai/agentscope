# -*- coding: utf-8 -*-
"""The embedding model configuration, used as DTO layer."""

from pydantic import BaseModel, Field

from ....embedding import EmbeddingModelCard


class ListEmbeddingModelsResponse(BaseModel):
    """List the candidate embedding models response."""

    models: list[EmbeddingModelCard] = Field(
        description="The candidate embedding models.",
    )
    total: int = Field(description="The total number of candidates.")


class ListEmbeddingModelsRequest(BaseModel):
    """List the candidate embedding models request."""

    provider: str = Field(
        description="The provider type, e.g. openai, dashscope, etc.",
    )
