# -*- coding: utf-8 -*-
"""Realtime model and WebRTC negotiation schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from ....realtime import RealtimeModelCard


class ListRealtimeModelsRequest(BaseModel):
    """List realtime models for one credential provider."""

    provider: str = Field(
        description="The credential provider type.",
    )


class ListRealtimeModelsResponse(BaseModel):
    """Realtime model candidates exposed by a credential provider."""

    models: list[RealtimeModelCard] = Field(
        description="The candidate realtime models.",
    )
    total: int = Field(description="The total number of candidates.")


class RealtimeConfigResponse(BaseModel):
    """Browser-facing WebRTC configuration."""

    ice_servers: list[dict] = Field(
        description="RTCIceServer-compatible dictionaries.",
    )


class RealtimeOfferRequest(BaseModel):
    """A browser WebRTC offer for a persisted session."""

    agent_id: str = Field(description="The agent that owns the session.")
    sdp: str = Field(description="The browser's SDP offer.")
    type: Literal["offer"] = "offer"


class RealtimeOfferResponse(BaseModel):
    """The server WebRTC answer."""

    connection_id: str = Field(
        description="The server-side realtime connection identifier.",
    )
    sdp: str = Field(description="The server's SDP answer.")
    type: Literal["answer"] = "answer"
