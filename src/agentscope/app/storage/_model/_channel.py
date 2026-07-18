# -*- coding: utf-8 -*-
"""The channel storage model."""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ....permission import PermissionMode


DmScope = Literal["MAIN", "PER_PEER", "PER_CHAT", "PER_CHANNEL_PEER"]

CHANNEL_ALLOWED_PERMISSION_MODES = frozenset(
    {PermissionMode.DEFAULT, PermissionMode.DONT_ASK, PermissionMode.BYPASS},
)


class ChannelRoutingRule(BaseModel):
    """A routing rule that maps metadata to an agent."""

    metadata_key: str
    metadata_value: str
    agent_id: str
    priority: int = 0


class ChannelRecord(BaseModel):
    """Persistent record for a channel instance.

    Unlike other records, ``channel_id`` is user-specified rather than
    auto-generated, so we don't extend ``_RecordBase``.
    """

    channel_id: str
    """User-specified unique identifier."""

    channel_type: str
    """Platform adapter type: feishu / dingtalk / discord / wecom."""

    platform_bot_id: str
    """Platform-scoped unique bot identifier (for dedup)."""

    credentials: dict[str, Any] = Field(default_factory=dict)
    """Platform credentials (app_id, app_secret, etc.)."""

    default_agent_id: str
    """Default bound agent_id when no routing rule matches."""

    chat_model_config: dict[str, Any] | None = None
    """Model configuration for channel-created sessions."""

    fallback_chat_model_config: dict[str, Any] | None = None
    """Fallback model configuration. Used when the primary model fails."""

    routing_rules: list[ChannelRoutingRule] = Field(default_factory=list)

    dm_scope: DmScope = "PER_PEER"
    """Session isolation strategy."""

    permission_mode: str = PermissionMode.DONT_ASK.value
    """Permission mode for channel sessions.

    Only a subset of ``PermissionMode`` values is allowed for channels:
    ``default``, ``dont_ask``, ``bypass``. Modes like ``accept_edits``
    and ``explore`` are designed for local IDE interactions and are not
    suitable for external messaging platforms.
    """

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, v: str) -> str:
        mode = PermissionMode(v)
        if mode not in CHANNEL_ALLOWED_PERMISSION_MODES:
            allowed = ", ".join(
                m.value for m in CHANNEL_ALLOWED_PERMISSION_MODES
            )
            raise ValueError(
                f"Permission mode '{v}' is not allowed for channels. "
                f"Allowed modes: {allowed}",
            )
        return v

    enabled: bool = True

    config: dict[str, Any] = Field(default_factory=dict)
    """Platform-specific configuration."""

    filter_tool_messages: bool = False
    filter_thinking_messages: bool = True

    tenant_user_id: str = ""
    """AgentScope user who owns this channel (multi-tenant)."""
