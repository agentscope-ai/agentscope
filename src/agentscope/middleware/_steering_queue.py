# -*- coding: utf-8 -*-
"""Input sources and receipts for cooperative reply steering."""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from .._utils._common import _generate_id
from ..message import Msg


@dataclass(frozen=True)
class SteeringInput:
    """One ordered steering input with a source-owned identifier.

    Args:
        id: Identifier used to acknowledge or reject this input.
        messages: Messages whose text and data blocks guide the reply.
    """

    id: str
    messages: list[Msg]


class SteeringSource(ABC):
    """Supply reply-scoped inputs without coupling agents to a transport.

    Reads must be non-destructive. Acknowledgement removes only the supplied
    inputs and must raise before committing if it cannot accept the change.
    Lifecycle hooks are optional for externally managed sources.
    """

    async def start_reply(self, session_id: str, reply_id: str) -> None:
        """Open an input window when a reply starts or resumes."""

    async def finish_reply(self, session_id: str, reply_id: str) -> None:
        """Close an input window and settle its unconsumed inputs."""

    @abstractmethod
    async def read(
        self,
        session_id: str,
        reply_id: str,
    ) -> list[SteeringInput]:
        """Read pending inputs for this reply in arrival order."""

    @abstractmethod
    async def acknowledge(
        self,
        session_id: str,
        reply_id: str,
        inputs: list[SteeringInput],
    ) -> None:
        """Acknowledge inputs appended to context without consuming others."""

    @abstractmethod
    async def reject(
        self,
        session_id: str,
        reply_id: str,
        item: SteeringInput,
        error: str,
    ) -> None:
        """Record an input that cannot be injected into context."""


@dataclass
class SteeringReceipt:
    """Track acceptance separately from context injection.

    Args:
        id: Identifier assigned to the accepted input.

    Attributes:
        status: Pending, injected, unconsumed or failed. Injection means the
            input reached context, not that a model followed the instruction.
        error: Validation error for a failed input, otherwise None.
    """

    id: str
    status: Literal["pending", "injected", "unconsumed", "failed"] = field(
        default="pending",
        init=False,
    )
    error: str | None = field(default=None, init=False)
    _done: asyncio.Event = field(
        default_factory=asyncio.Event,
        init=False,
        repr=False,
    )

    async def wait(self) -> str:
        """Wait for a terminal status; cancelling this wait keeps delivery."""
        await self._done.wait()
        return self.status

    def _finish(
        self,
        status: Literal["injected", "unconsumed", "failed"],
        error: str | None = None,
    ) -> None:
        """Settle a receipt after its input leaves the source."""
        self.status = status
        self.error = error
        self._done.set()


# Receipts are settled only by their owning queue.
# pylint: disable=protected-access
class SteeringQueue(SteeringSource):
    """An in-process source used on the same event loop as the agent.

    Attach it to a SteeringMiddleware, then submit after ReplyStartEvent.
    The queue is not persistent or thread-safe. A reply that ends or parks
    leaves unconsumed inputs with the caller rather than starting a new turn.
    """

    def __init__(self) -> None:
        """Initialize isolated input windows for active replies."""
        self._pending: dict[
            tuple[str, str],
            dict[str, tuple[SteeringInput, SteeringReceipt]],
        ] = {}

    def submit(
        self,
        inputs: Msg | list[Msg],
        *,
        session_id: str,
        reply_id: str,
    ) -> SteeringReceipt:
        """Accept copied messages for an active reply without waiting.

        Args:
            inputs: One message or a non-empty list of messages.
            session_id: Session of the active reply.
            reply_id: Reply observed by the caller.

        Returns:
            A receipt that can be awaited with its wait method.

        Raises:
            ValueError: The reply is inactive or the message list is empty.
        """
        key = (session_id, reply_id)
        if key not in self._pending:
            raise ValueError(f"Reply '{reply_id}' is not accepting steering.")
        messages = inputs if isinstance(inputs, list) else [inputs]
        if not messages:
            raise ValueError("Steering requires at least one message.")
        item = SteeringInput(
            id=_generate_id(),
            messages=[message.model_copy(deep=True) for message in messages],
        )
        receipt = SteeringReceipt(item.id)
        self._pending[key][item.id] = (item, receipt)
        return receipt

    async def start_reply(self, session_id: str, reply_id: str) -> None:
        """Open a reply window, rejecting overlapping uses of its identity."""
        key = (session_id, reply_id)
        if key in self._pending:
            raise ValueError(f"Reply '{reply_id}' is already accepting input.")
        self._pending[key] = {}

    async def finish_reply(self, session_id: str, reply_id: str) -> None:
        """Return all remaining inputs to their callers as unconsumed."""
        for _, receipt in self._pending.pop(
            (session_id, reply_id),
            {},
        ).values():
            receipt._finish("unconsumed")

    async def read(
        self,
        session_id: str,
        reply_id: str,
    ) -> list[SteeringInput]:
        """Return pending inputs without transferring ownership."""
        pending = self._pending.get((session_id, reply_id), {})
        return [item for item, _ in pending.values()]

    async def acknowledge(
        self,
        session_id: str,
        reply_id: str,
        inputs: list[SteeringInput],
    ) -> None:
        """Settle the specified inputs after context injection."""
        pending = self._pending.get((session_id, reply_id), {})
        if any(item.id not in pending for item in inputs):
            raise LookupError("The steering inputs are no longer pending.")
        for item in inputs:
            _, receipt = pending.pop(item.id)
            receipt._finish("injected")

    async def reject(
        self,
        session_id: str,
        reply_id: str,
        item: SteeringInput,
        error: str,
    ) -> None:
        """Settle one invalid input without affecting other pending inputs."""
        pending = self._pending.get((session_id, reply_id), {})
        entry = pending.pop(item.id, None)
        if entry is not None:
            entry[1]._finish("failed", error)
