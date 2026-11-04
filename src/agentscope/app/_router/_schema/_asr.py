# -*- coding: utf-8 -*-
"""Speech recognition request and response schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from ....asr import ASRModelCard


class ListASRModelsResponse(BaseModel):
    """List the candidate ASR models response."""

    models: list[ASRModelCard] = Field(
        description="The candidate ASR models.",
    )
    total: int = Field(description="The total number of candidates.")


class ListASRModelsRequest(BaseModel):
    """List the candidate ASR models request."""

    provider: str = Field(
        description="The credential provider type.",
    )


class TranscriptionResponse(BaseModel):
    """Text returned after transcribing an uploaded audio file."""

    text: str = Field(description="The transcribed text.")


class RealtimeASRTokenRequest(BaseModel):
    """Request a capability for one realtime ASR configuration."""

    credential_id: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RealtimeASRTokenResponse(BaseModel):
    """Short-lived capability accepted by the realtime ASR socket."""

    token: str = Field(description="Pass as the WebSocket token query.")
    expires_at: int = Field(
        description="Unix timestamp after which the token is refused.",
    )


class RealtimeASRStartRequest(BaseModel):
    """First browser frame for a realtime ASR connection."""

    type: Literal["start"]
    credential_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)
    sample_rate: int = Field(gt=0)
