# -*- coding: utf-8 -*-
"""Request / response schemas for the channel router."""
from datetime import datetime

from pydantic import BaseModel, Field

from ...storage import (
    RoutingConfig,
    SessionRecord,
    SessionSettings,
)


class CreateChannelRequest(BaseModel):
    """Request body for creating a channel."""

    channel_type: str = Field(description="Channel type id, e.g. 'feishu'.")
    name: str | None = Field(
        default=None,
        description="Optional display name.",
    )
    credentials: dict = Field(description="Platform credentials.")
    platform_config: dict = Field(
        default_factory=dict,
        description="Non-secret platform options.",
    )
    routing: RoutingConfig = Field(description="Inbound routing rules.")
    session: SessionSettings = Field(description="Session/model settings.")
    enabled: bool = Field(default=True, description="Start it enabled.")


class UpdateChannelRequest(BaseModel):
    """Request body for updating a channel (type/credentials immutable)."""

    name: str | None = None
    platform_config: dict | None = None
    routing: RoutingConfig | None = None
    session: SessionSettings | None = None
    enabled: bool | None = None


class ChannelResponse(BaseModel):
    """Channel details returned to the client (credentials omitted)."""

    id: str
    channel_type: str
    name: str | None
    user_id: str
    platform_bot_id: str
    enabled: bool
    platform_config: dict
    routing: RoutingConfig
    session: SessionSettings
    created_at: datetime
    updated_at: datetime


class ChannelActionResponse(BaseModel):
    """Result of an enable/disable action."""

    status: str = Field(description="New lifecycle status, e.g. 'enabled'.")


class ChannelSessionsResponse(BaseModel):
    """Sessions a channel has spawned."""

    sessions: list[SessionRecord]
    total: int


class ChannelChatId(BaseModel):
    """A chat the bot can route to (from the platform or seen inbound)."""

    chat_id: str
    name: str = ""
    source: str = Field(description="'platform' or 'recorded'.")


class ChannelChatIdsResponse(BaseModel):
    """Chats available for routing configuration."""

    chats: list[ChannelChatId] = Field(default_factory=list)
