# -*- coding: utf-8 -*-
"""The AGUI middleware class."""

from ._base import ProtocolMiddlewareBase
from ....event import AgentEvent


class AGUIProtocolMiddleware(ProtocolMiddlewareBase):
    """The middleware that converts the AgentScope events into AGUI
    protocol."""

    def _convert_to_protocol(self, event: AgentEvent) -> dict:
        """Convert the AgentScope events into AGUI protocol."""
        # TODO: the AGUI protocol
