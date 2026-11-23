# -*- coding: utf-8 -*-
"""Adapt the durable chat queue to SDK steering middleware."""
from pydantic import TypeAdapter

from .._bus_ops import (
    acknowledge_steering_chat_inputs,
    fail_steering_chat_input,
    list_steering_chat_inputs,
    publish_session_event,
)
from ..message_bus import MessageBus
from ..._logging import logger
from ...event import CustomEvent
from ...message import Msg
from ...middleware import (
    SteeringInput,
    SteeringMiddleware as SDKSteeringMiddleware,
    SteeringSource,
)

_MESSAGE_INPUT_ADAPTER: TypeAdapter = TypeAdapter(Msg | list[Msg])


class _ChatQueueSteeringSource(SteeringSource):
    """Read reserved inputs; ChatService owns the active-reply lifecycle."""

    def __init__(self, message_bus: MessageBus) -> None:
        """Store the application's durable bus."""
        self._bus = message_bus

    async def read(
        self,
        session_id: str,
        reply_id: str,
    ) -> list[SteeringInput]:
        """Hydrate reserved items, retaining invalid ones as failed."""
        items = await list_steering_chat_inputs(
            self._bus,
            session_id,
            reply_id,
        )
        inputs = []
        for item in items:
            try:
                parsed = _MESSAGE_INPUT_ADAPTER.validate_python(item["input"])
            except ValueError as exc:
                error = f"The queued message could not be injected: {exc}"
                await fail_steering_chat_input(
                    self._bus,
                    session_id,
                    reply_id,
                    item["id"],
                    error,
                )
                await self._notify(
                    session_id,
                    "chat_input_steer_failed",
                    item["id"],
                    self._message_ids(item["input"]),
                    error,
                )
            else:
                inputs.append(
                    SteeringInput(
                        id=item["id"],
                        messages=parsed
                        if isinstance(parsed, list)
                        else [parsed],
                    ),
                )
        return inputs

    async def acknowledge(
        self,
        session_id: str,
        reply_id: str,
        inputs: list[SteeringInput],
    ) -> None:
        """Remove injected inputs and publish the existing WebUI events."""
        await acknowledge_steering_chat_inputs(
            self._bus,
            session_id,
            reply_id,
            [item.id for item in inputs],
        )
        for item in inputs:
            await self._notify(
                session_id,
                "chat_input_injected",
                item.id,
                [message.id for message in item.messages],
            )

    async def reject(
        self,
        session_id: str,
        reply_id: str,
        item: SteeringInput,
        error: str,
    ) -> None:
        """Keep unsupported content editable in the durable queue."""
        await fail_steering_chat_input(
            self._bus,
            session_id,
            reply_id,
            item.id,
            error,
        )
        await self._notify(
            session_id,
            "chat_input_steer_failed",
            item.id,
            [message.id for message in item.messages],
            error,
        )

    async def _notify(
        self,
        session_id: str,
        name: str,
        item_id: str,
        message_ids: list[str],
        error: str | None = None,
    ) -> None:
        """Do not roll back a committed injection if notification fails."""
        value: dict = {"queue_item_id": item_id, "message_ids": message_ids}
        if error is not None:
            value["message"] = error
        try:
            await publish_session_event(
                self._bus,
                session_id,
                CustomEvent(name=name, value=value).model_dump(mode="json"),
            )
        except Exception:
            logger.exception(
                "Steering transition committed; notification failed for %s.",
                item_id,
            )

    @staticmethod
    def _message_ids(raw_input: object) -> list[str]:
        """Extract available message ids from a malformed queued input."""
        messages = raw_input if isinstance(raw_input, list) else [raw_input]
        return [
            str(message["id"])
            for message in messages
            if isinstance(message, dict) and message.get("id")
        ]


class SteeringMiddleware(SDKSteeringMiddleware):
    """Use SDK steering with the application's queue and event protocol.

    Args:
        message_bus: Application message bus holding reserved chat inputs.
    """

    def __init__(self, message_bus: MessageBus) -> None:
        """Preserve the service constructor and WebUI hint source."""
        super().__init__(
            _ChatQueueSteeringSource(message_bus),
            hint_source="chat_input_steering",
        )
