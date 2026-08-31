# -*- coding: utf-8 -*-
"""Channel type registry — the set of platform types a service allows.

A channel *type* is fully described by its channel class (see
:class:`~agentscope.app.channel.ChannelBase`): the class carries its
``channel_type`` id, ``platform_bot_id_field``, and nested
``Credentials`` / ``Config`` models. The registry is built from the
channel classes passed to :func:`~agentscope.app.create_app` via
``channels=[...]`` and exposes them to the service — frontend form
schemas, bot-uniqueness checks, and per-record instance construction.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from ._base import ChannelBase


class ChannelTypeSchema(BaseModel):
    """Frontend-facing metadata for one channel type.

    Generated from a channel class. ``credentials_schema`` /
    ``config_schema`` are JSON Schemas the frontend renders as forms.
    """

    channel_type: str
    display_name: str
    description: str = ""
    icon_url: str = ""
    credentials_schema: dict
    config_schema: dict
    platform_bot_id_field: str


class ChannelTypeRegistry:
    """The channel types a service allows, keyed by ``channel_type``."""

    def __init__(
        self,
        channels: list[type["ChannelBase"]] | None = None,
    ) -> None:
        """Build the registry from a list of channel classes.

        Args:
            channels (`list[type[ChannelBase]] | None`):
                Channel classes to allow. Passed through from
                ``create_app(channels=[...])``.
        """
        self._classes: dict[str, type["ChannelBase"]] = {}
        for channel_cls in channels or []:
            self.register(channel_cls)

    def __bool__(self) -> bool:
        """Whether any channel type is registered (feature enabled)."""
        return bool(self._classes)

    def register(self, channel_cls: type["ChannelBase"]) -> None:
        """Register a channel class under its ``channel_type``.

        Args:
            channel_cls (`type[ChannelBase]`): The channel class to add.
        """
        channel_type = channel_cls.channel_type
        if not channel_type:
            raise ValueError(
                f"{channel_cls.__name__} must set a non-empty "
                f"'channel_type' to be registered.",
            )
        self._classes[channel_type] = channel_cls

    def get(self, channel_type: str) -> type["ChannelBase"] | None:
        """Return the channel class for a type, or ``None``.

        Args:
            channel_type (`str`): The platform type id.
        """
        return self._classes.get(channel_type)

    def has_type(self, channel_type: str) -> bool:
        """Whether a type is registered.

        Args:
            channel_type (`str`): The platform type id.
        """
        return channel_type in self._classes

    def create_channel(
        self,
        channel_type: str,
        channel_id: str,
        credentials: dict,
        config: dict,
    ) -> "ChannelBase":
        """Validate stored credentials/config against the class's nested
        models and build the channel instance.

        Args:
            channel_type (`str`): The platform type id.
            channel_id (`str`): The instance's unique id.
            credentials (`dict`): Raw credentials to validate.
            config (`dict`): Raw platform options to validate.

        Raises:
            `ValueError`: If ``channel_type`` is not registered.
        """
        channel_cls = self._classes.get(channel_type)
        if channel_cls is None:
            raise ValueError(
                f"Channel type '{channel_type}' is not registered; pass it "
                f"to create_app(channels=[...]).",
            )
        return channel_cls(
            channel_id,
            channel_cls.Credentials(**credentials),
            channel_cls.Config(**config),
        )

    def schema_of(
        self,
        channel_cls: type["ChannelBase"],
    ) -> ChannelTypeSchema:
        """Build the frontend schema for one channel class.

        Args:
            channel_cls (`type[ChannelBase]`): The channel class.
        """
        return ChannelTypeSchema(
            channel_type=channel_cls.channel_type,
            display_name=channel_cls.display_name or channel_cls.channel_type,
            description=channel_cls.description,
            icon_url=channel_cls.icon_url,
            credentials_schema=channel_cls.Credentials.model_json_schema(),
            config_schema=channel_cls.Config.model_json_schema(),
            platform_bot_id_field=channel_cls.platform_bot_id_field,
        )

    def list_types(self) -> list[ChannelTypeSchema]:
        """List frontend schemas for all registered types."""
        return [self.schema_of(c) for c in self._classes.values()]

    def extract_platform_bot_id(
        self,
        channel_type: str,
        credentials: dict,
    ) -> str:
        """Read the bot-identifying credential field, for uniqueness.

        Args:
            channel_type (`str`): The platform type id.
            credentials (`dict`): Raw credentials to read the bot id from.
        """
        channel_cls = self._classes.get(channel_type)
        if channel_cls is None or not channel_cls.platform_bot_id_field:
            raise ValueError(
                f"Cannot extract platform_bot_id for type '{channel_type}'.",
            )
        bot_id = credentials.get(channel_cls.platform_bot_id_field)
        if not bot_id:
            raise ValueError(
                f"Missing '{channel_cls.platform_bot_id_field}' in "
                f"credentials.",
            )
        return str(bot_id)
