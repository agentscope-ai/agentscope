# -*- coding: utf-8 -*-
"""The realtime model configuration, used as DTO layer."""

from pydantic import BaseModel, Field

from ....realtime import RealtimeModelCard


class ListRealtimeModelsResponse(BaseModel):
    """List the candidate realtime models response."""

    models: list[RealtimeModelCard] = Field(
        description="The candidate realtime models.",
    )
    total: int = Field(description="The total number of candidates.")


class ListRealtimeModelsRequest(BaseModel):
    """List the candidate realtime models request."""

    provider: str = Field(
        description="The provider type, e.g. dashscope_credential.",
    )
