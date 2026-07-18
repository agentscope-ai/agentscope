# -*- coding: utf-8 -*-
"""Channel module custom exceptions."""


class ChannelError(Exception):
    """Base exception for the channel module."""


class ChannelNotFoundError(ChannelError):
    """Raised when a channel record is not found."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(f"Channel '{channel_id}' not found.")
        self.channel_id = channel_id


class DuplicateBotError(ChannelError):
    """Raised when registering a platform bot that is already bound."""

    def __init__(self, platform_bot_id: str, existing_channel_id: str) -> None:
        super().__init__(
            f"Bot '{platform_bot_id}' already registered "
            f"as channel '{existing_channel_id}'.",
        )
        self.platform_bot_id = platform_bot_id
        self.existing_channel_id = existing_channel_id


class ChannelConnectionError(ChannelError):
    """Raised when a channel fails to establish platform connection."""
