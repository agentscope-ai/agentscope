# -*- coding: utf-8 -*-
"""Channel module configuration."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChannelSessionDefaults(BaseModel):
    """Module-level default values for channel-created sessions.

    These are **not** a replacement for ``SessionConfig`` (defined in
    ``agentscope.app.storage``).  Instead, they provide fallback values
    that ``ChannelGateway._ensure_session`` uses when constructing a real
    ``SessionConfig`` for a new channel session — specifically
    ``workspace_id`` and ``chat_model_config``.

    Per-channel overrides in ``ChannelRecord.chat_model_config`` take
    precedence over the defaults here.
    """

    workspace_id: str = "default"
    """Workspace to use for channel-created sessions."""

    chat_model_config: dict[str, Any] | None = None
    """Default chat model config dict.  Channels without an explicit
    per-channel ``chat_model_config`` fall back to this."""


class ChannelConfig(BaseModel):
    """Module-level configuration for the Channel subsystem."""

    response_timeout: float = 60.0
    """Maximum seconds to wait for an agent reply."""

    concurrent_users_limit: int = 1000
    """Per-node concurrency semaphore for simultaneous event processing.
    Per-user serialization is handled by distributed locks via MessageBus."""

    default_session_config: ChannelSessionDefaults = Field(
        default_factory=ChannelSessionDefaults,
    )
    """Fallback values for channel-created sessions (workspace and model)."""
