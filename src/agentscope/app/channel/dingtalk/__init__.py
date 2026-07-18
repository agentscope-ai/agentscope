# -*- coding: utf-8 -*-
"""DingTalk channel adapter (not yet implemented)."""
from typing import Any

from .._base import ChannelBase


class DingTalkChannel(ChannelBase):
    """Placeholder — DingTalk adapter is planned but not yet implemented."""

    @property
    def channel_id(self) -> str:
        return ""

    def __init__(self, **kwargs: Any) -> None:
        raise NotImplementedError(
            "DingTalkChannel is not yet implemented. "
            "See docs/design_channel_v3.md for the planned design.",
        )

    async def start_listening(self) -> None:
        raise NotImplementedError

    async def send_response(self, event: Any, response: str) -> None:
        raise NotImplementedError
