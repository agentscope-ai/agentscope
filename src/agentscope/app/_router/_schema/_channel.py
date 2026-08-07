# -*- coding: utf-8 -*-
"""Request / response schemas for the channel router."""

from pydantic import BaseModel, Field, model_validator

from ...channel import (
    ChannelCredentialBindingSession,
    ChannelCredentialBindingStatus,
)

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
    credentials: dict | None = Field(
        default=None,
        description="Platform credentials for manual setup.",
    )
    credential_binding_id: str | None = Field(
        default=None,
        description="Authorized QR binding session, instead of credentials.",
    )
    platform_config: dict = Field(
        default_factory=dict,
        description="Non-secret platform options.",
    )
    routing: RoutingConfig = Field(description="Inbound routing rules.")
    session: SessionSettings = Field(description="Session/model settings.")
    enabled: bool = Field(default=True, description="Start it enabled.")

    @model_validator(mode="after")
    def _exactly_one_credential_source(self) -> "CreateChannelRequest":
        """Require either manual credentials or one QR binding session."""
        if (self.credentials is None) == (self.credential_binding_id is None):
            raise ValueError(
                "Provide exactly one of 'credentials' or "
                "'credential_binding_id'.",
            )
        return self


class StartChannelCredentialBindingRequest(BaseModel):
    """Start a QR-code binding session for a channel type."""

    channel_type: str


class ChannelCredentialBindingSessionResponse(
    ChannelCredentialBindingSession,
):
    """Binding session plus its channel type for the generic WebUI."""

    channel_type: str


class ChannelCredentialBindingStatusResponse(
    ChannelCredentialBindingStatus,
):
    """Binding poll response plus its channel type."""

    channel_type: str


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
    created_at: str
    updated_at: str


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
