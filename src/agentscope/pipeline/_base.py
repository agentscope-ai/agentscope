# -*- coding: utf-8 -*-
"""The base pipeline protocol."""


from typing import Protocol, AsyncGenerator, Any

from ..event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    AgentEvent,
)
from ..message import Msg


class PipelineProtocol(Protocol):
    """The base pipeline protocol."""

    async def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent,
        **kwargs: Any,
    ) -> AsyncGenerator[AgentEvent | Any, None]:
        """Run the pipeline."""
