# -*- coding: utf-8 -*-
"""Channel type registry — maps channel_type names to metadata and
JSON Schema definitions for frontend form generation."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ._base import ChannelBase

ChannelFactory = Callable[..., "ChannelBase"]


# ── Platform credential / config models ──


class FeishuCredentials(BaseModel):
    """Credentials for a Feishu (Lark) bot application."""

    app_id: str = Field(title="App ID", description="Feishu App ID")
    app_secret: str = Field(
        title="App Secret",
        description="Feishu App Secret",
        json_schema_extra={"format": "password"},
    )


class FeishuChannelConfig(BaseModel):
    """Platform-specific configuration for a Feishu channel."""

    only_at_reply: bool = Field(
        default=True,
        title="Reply only when mentioned",
        description="In group chats, reply only when the bot is @mentioned",
    )


class DiscordCredentials(BaseModel):
    """Credentials for a Discord bot."""

    bot_token: str = Field(
        title="Bot Token",
        json_schema_extra={"format": "password"},
    )
    application_id: str = Field(title="Application ID")


class DingTalkCredentials(BaseModel):
    """Credentials for a DingTalk bot (Stream mode)."""

    client_id: str = Field(title="Client ID (AppKey)")
    client_secret: str = Field(
        title="Client Secret (AppSecret)",
        json_schema_extra={"format": "password"},
    )


class WeComCredentials(BaseModel):
    """Credentials for a WeCom bot."""

    corp_id: str = Field(title="Corp ID")
    agent_id: str = Field(title="Agent ID")
    secret: str = Field(
        title="Secret",
        json_schema_extra={"format": "password"},
    )


# ── Schema and registry ──


class ChannelTypeSchema(BaseModel):
    """Metadata and credential schema for a channel type.

    For built-in platforms, ``credentials_schema`` and ``config_schema``
    are auto-generated from Pydantic models (e.g. ``FeishuCredentials``)
    via ``model_json_schema()``.  Third-party plugins may still supply
    raw JSON Schema dicts directly.
    """

    channel_type: str
    """Type identifier (e.g. 'feishu', 'discord')."""

    display_name: str
    """Human-readable name for the UI."""

    description: str = ""
    """Brief description of the platform."""

    credentials_schema: dict[str, Any] = Field(default_factory=dict)
    """JSON Schema describing required credentials fields."""

    config_schema: dict[str, Any] = Field(default_factory=dict)
    """JSON Schema describing optional platform-specific config."""

    platform_bot_id_field: str = ""
    """Which credential field holds the platform_bot_id."""


class ChannelTypeRegistry:
    """Registry of supported channel types and their schemas."""

    def __init__(self) -> None:
        self._types: dict[str, ChannelTypeSchema] = {}
        self._factories: dict[str, ChannelFactory] = {}
        self._register_builtin_types()

    def register(
        self,
        schema: ChannelTypeSchema,
        factory: ChannelFactory | None = None,
    ) -> None:
        """Register a new channel type with optional factory function."""
        self._types[schema.channel_type] = schema
        if factory is not None:
            self._factories[schema.channel_type] = factory

    def register_factory(
        self,
        channel_type: str,
        factory: ChannelFactory,
    ) -> None:
        """Register a factory function for an existing channel type."""
        self._factories[channel_type] = factory

    def create_channel(
        self,
        channel_type: str,
        channel_id: str,
        credentials: dict,
        config: dict,
    ) -> ChannelBase:
        """Create a channel instance using the registered factory.

        Raises ValueError if no factory is registered for the type.
        """
        factory = self._factories.get(channel_type)
        if factory is None:
            raise ValueError(
                f"No factory registered for channel type '{channel_type}'. "
                f"Use registry.register(schema, factory=...) to register.",
            )
        return factory(
            channel_id=channel_id,
            credentials=credentials,
            config=config,
        )

    def has_factory(self, channel_type: str) -> bool:
        """Check if a factory is registered for the given type."""
        return channel_type in self._factories

    def get(self, channel_type: str) -> ChannelTypeSchema | None:
        """Get schema for a channel type."""
        return self._types.get(channel_type)

    def list_types(self) -> list[ChannelTypeSchema]:
        """Return all registered types."""
        return list(self._types.values())

    def extract_platform_bot_id(
        self,
        channel_type: str,
        credentials: dict,
    ) -> str:
        """Extract platform_bot_id from credentials using the schema."""
        schema = self._types.get(channel_type)
        if not schema or not schema.platform_bot_id_field:
            raise ValueError(
                f"Cannot extract platform_bot_id for type '{channel_type}'",
            )
        bot_id = credentials.get(schema.platform_bot_id_field)
        if not bot_id:
            raise ValueError(
                f"Missing '{schema.platform_bot_id_field}' in credentials",
            )
        return str(bot_id)

    def _register_builtin_types(self) -> None:
        """Register built-in platform types.

        Credentials and config schemas are generated from the Pydantic
        models defined above.
        """
        self.register(
            ChannelTypeSchema(
                channel_type="feishu",
                display_name="Feishu (Lark)",
                description="Feishu bot via WebSocket long-connection mode",
                credentials_schema=FeishuCredentials.model_json_schema(),
                config_schema=FeishuChannelConfig.model_json_schema(),
                platform_bot_id_field="app_id",
            ),
        )

        self.register(
            ChannelTypeSchema(
                channel_type="discord",
                display_name="Discord",
                description="Discord bot via Gateway WebSocket",
                credentials_schema=DiscordCredentials.model_json_schema(),
                platform_bot_id_field="application_id",
            ),
        )

        self.register(
            ChannelTypeSchema(
                channel_type="dingtalk",
                display_name="DingTalk",
                description="DingTalk bot via Stream mode",
                credentials_schema=DingTalkCredentials.model_json_schema(),
                platform_bot_id_field="client_id",
            ),
        )

        self.register(
            ChannelTypeSchema(
                channel_type="wecom",
                display_name="WeCom",
                description="WeCom enterprise application bot",
                credentials_schema=WeComCredentials.model_json_schema(),
                platform_bot_id_field="corp_id",
            ),
        )

        self._register_builtin_factories()

    def _register_builtin_factories(self) -> None:
        """Register factory functions for built-in channel types.

        Each factory validates credentials/config against the
        corresponding Pydantic model before constructing the adapter.
        """

        def _feishu_factory(
            channel_id: str,
            credentials: dict,
            config: dict,
        ) -> ChannelBase:
            from .feishu import FeishuChannel

            creds = FeishuCredentials(**credentials)
            cfg = FeishuChannelConfig(**config)
            return FeishuChannel(
                channel_id=channel_id,
                app_id=creds.app_id,
                app_secret=creds.app_secret,
                only_at_reply=cfg.only_at_reply,
            )

        def _discord_factory(
            channel_id: str,
            credentials: dict,
            config: dict,
        ) -> ChannelBase:
            from .discord import DiscordChannel

            creds = DiscordCredentials(**credentials)
            return DiscordChannel(
                channel_id=channel_id,
                bot_token=creds.bot_token,
                **config,
            )

        def _dingtalk_factory(
            channel_id: str,
            credentials: dict,
            config: dict,
        ) -> ChannelBase:
            from .dingtalk import DingTalkChannel

            creds = DingTalkCredentials(**credentials)
            return DingTalkChannel(
                channel_id=channel_id,
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                **config,
            )

        def _wecom_factory(
            channel_id: str,
            credentials: dict,
            config: dict,
        ) -> ChannelBase:
            from .wecom import WeChatWorkChannel

            creds = WeComCredentials(**credentials)
            return WeChatWorkChannel(
                channel_id=channel_id,
                corp_id=creds.corp_id,
                agent_id=creds.agent_id,
                secret=creds.secret,
                **config,
            )

        self.register_factory("feishu", _feishu_factory)
        self.register_factory("discord", _discord_factory)
        self.register_factory("dingtalk", _dingtalk_factory)
        self.register_factory("wecom", _wecom_factory)
