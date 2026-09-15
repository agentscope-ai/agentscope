# -*- coding: utf-8 -*-
"""The channel storage model. Key points:

- Routing is one concept: an ordered list of :class:`ChannelBinding`.
  Each rule matches an inbound event and yields two outputs — which
  agent handles it and how the session is grouped.
- ``(agent_id, session_id)`` is derived by a pure function (see
  :func:`agentscope.app.channel.resolve`); there is no persisted
  channel→session mapping table.
- ``platform_bot_id`` is NOT stored — it is extracted from
  ``credentials`` on write for the uniqueness index.
"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ._base import _RecordBase
from ....permission import PermissionMode


class SessionScope(str, Enum):
    """How inbound messages are grouped into agent sessions.

    Scope is chat-centric: the session key is a projection of the
    ``(chat_id, user_id)`` pair.
    """

    PER_CHAT = "per_chat"
    """One session per chat. A direct message is naturally per-user
    (the chat has only that user); a group chat shares one session
    across all members."""

    PER_CHAT_USER = "per_chat_user"
    """One session per (chat, user). Only meaningful for group chats —
    isolates each member into their own session within the group."""


class ChannelBinding(BaseModel):
    """One routing rule: match an inbound event, then decide which agent
    handles it and how its session is grouped.

    Rules are evaluated in list order; the first match wins. The final
    rule must be a catch-all (``match_value == "*"``) — it carries the
    channel's defaults.
    """

    match_key: str = "chat_id"
    """Which field of the event to match: ``chat_id``, ``user_id``, or a
    key inside ``event.metadata`` (e.g. ``chat_type``)."""

    match_value: str = "*"
    """Exact value to match, or ``"*"`` to match anything (catch-all)."""

    agent_id: str
    """The agent that handles matched messages."""

    session_scope: SessionScope = SessionScope.PER_CHAT
    """How matched messages are grouped into sessions."""


class RoutingConfig(BaseModel):
    """Ordered routing rules for a channel.

    Validated so routing is always total and unambiguous: exactly one
    catch-all, placed last, with no duplicate ``(match_key,
    match_value)`` pairs.
    """

    bindings: list[ChannelBinding] = Field(default_factory=list)

    @field_validator("bindings")
    @classmethod
    def _validate_bindings(
        cls,
        bindings: list[ChannelBinding],
    ) -> list[ChannelBinding]:
        if not bindings:
            raise ValueError(
                "routing.bindings must contain at least a catch-all rule "
                "(match_value='*').",
            )
        # Exactly one catch-all, and it must be last.
        catch_all_indices = [
            i for i, b in enumerate(bindings) if b.match_value == "*"
        ]
        if len(catch_all_indices) != 1:
            raise ValueError(
                "routing.bindings must contain exactly one catch-all rule "
                "(match_value='*').",
            )
        if catch_all_indices[0] != len(bindings) - 1:
            raise ValueError(
                "The catch-all rule (match_value='*') must be the last "
                "binding; rules after it would be unreachable.",
            )
        # No duplicate (match_key, match_value) pairs.
        seen: set[tuple[str, str]] = set()
        for b in bindings:
            key = (b.match_key, b.match_value)
            if key in seen:
                raise ValueError(
                    f"Duplicate routing rule for {key}; the later one "
                    "would be unreachable.",
                )
            seen.add(key)
        return bindings


class SessionSettings(BaseModel):
    """Settings applied when a channel creates an agent session."""

    chat_model_config: dict[str, Any]
    """Model configuration for channel-created sessions. Required — there
    is no global fallback default."""

    fallback_chat_model_config: dict[str, Any] | None = None
    """Optional fallback model configuration, used when the primary
    model fails."""

    permission_mode: str = PermissionMode.DEFAULT.value
    """Permission mode for channel sessions. Defaults to ``default`` so a
    tool needing approval surfaces the confirmation card rather than being
    auto-denied. Any :class:`PermissionMode` is accepted — the engine
    resolves every mode server-side and the ASK path maps to the card."""

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, v: str) -> str:
        PermissionMode(v)  # reject values outside the enum
        return v


class ChannelRecord(_RecordBase):
    """Persistent record for a channel instance — the single source of
    truth. Running adapter instances are a projection of this record.

    ``id`` / ``created_at`` / ``updated_at`` come from
    :class:`_RecordBase`, so this record round-trips through the generic
    SQL row mapper like every other one. ``updated_at`` doubles as the
    reconcile version: the lifecycle dispatcher compares it to detect
    that a running instance's config has changed.
    """

    # -- Identity & ownership --

    channel_type: str
    """Platform adapter type: feishu / dingtalk / discord / wecom."""

    name: str | None = None
    """Optional display name, so two channels of the same platform are
    distinguishable in the UI."""

    user_id: str
    """AgentScope-side owner. Not platform data; used for access
    control (a user only sees their own channels)."""

    enabled: bool = True

    # -- Platform access --

    credentials: dict[str, Any] = Field(default_factory=dict)
    """Platform credentials (app_id, app_secret, ...). Fields marked
    ``format: password`` in the type's credential schema are secret.

    NOTE: at-rest encryption is a documented future hook; today these
    are masked at the API boundary. ``platform_bot_id`` is NOT stored
    here — it is extracted from these credentials on write for the
    uniqueness index.
    """

    platform_config: dict[str, Any] = Field(default_factory=dict)
    """Platform-specific configuration (e.g. only_at_reply for Feishu)."""

    # -- Business configuration --

    routing: RoutingConfig
    session: SessionSettings
