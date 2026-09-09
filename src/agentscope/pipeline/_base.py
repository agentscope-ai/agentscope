# -*- coding: utf-8 -*-
"""The base pipeline protocol."""

from typing import AsyncGenerator, Protocol, runtime_checkable

from pydantic import BaseModel

from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg


@runtime_checkable
class PipelineProtocol(Protocol):
    """What a pipeline has to offer to go where an agent goes.

    Two things: a reply that streams, and a state that can be put away.
    Whatever a pipeline needs to carry on from where it stopped lives in
    ``state`` — its own state only, not that of the agents inside it,
    which keep their own.

    ``reply_stream`` is declared as a plain ``def`` returning an async
    generator rather than an ``async def``: such a function is called,
    not awaited, and an ``async def`` here would be satisfied by neither
    ``Agent`` nor any pipeline.
    """

    @property
    def state(self) -> BaseModel:
        """The pipeline's own state, as plain data."""

    def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply to the given inputs and stream what happens."""
