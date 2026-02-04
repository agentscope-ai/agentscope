# -*- coding: utf-8 -*-
"""The realtime model base class."""
import asyncio
import json
from abc import abstractmethod
from asyncio import Queue
from typing import Any

import websockets
from websockets import ClientConnection

from ._events import ModelEvents
from ..message import AudioBlock, TextBlock, ImageBlock, ToolResultBlock


class RealtimeModelBase:
    """The realtime model base class."""

    model_name: str
    """The model name"""

    support_input_modalities: list[str]
    """The supported input modalities of the DashScope realtime model."""

    websocket_url: str
    """The websocket URL of the realtime model API."""

    websocket_headers: dict[str, str]
    """The websocket headers of the realtime model API."""

    input_sample_rate: int
    """The input audio sample rate."""

    output_sample_rate: int
    """The output audio sample rate."""

    def __init__(
        self,
        model_name: str,
    ) -> None:
        """Initialize the RealtimeModelBase class.

        Args:
            model_name (`str`):
                The model name.
        """

        self.model_name = model_name

        # The incoming queue to handle the data returned from the realtime
        # model API.
        self._incoming_queue = Queue()
        self._incoming_task = None

        self._websocket: ClientConnection | None = None

    @abstractmethod
    async def send(
        self,
        data: AudioBlock | TextBlock | ImageBlock | ToolResultBlock,
    ) -> None:
        """Send data to the realtime model for processing.

        Args:
            data (`AudioBlock | TextBlock | ImageBlock | ToolResultBlock`):
                The data to be sent to the realtime model.
        """

    async def connect(
        self,
        outgoing_queue: Queue,
        instructions: str,
        tools: list[dict] | None = None,
    ) -> None:
        """Establish a connection to the realtime model.

        Args:
            outgoing_queue (`Queue`):
                The queue to push the model responses to the outside.
            instructions (`str`):
                The instructions to guide the realtime model's behavior.
            tools (`list[dict]`, *optional*):
                The list of tools JSON schemas.
        """

        self._websocket = await websockets.connect(
            self.websocket_url,
            additional_headers=self.websocket_headers,
        )

        self._incoming_task = asyncio.create_task(
            self._receive_model_event_loop(outgoing_queue),
        )

        # Updating the session with instructions and other configurations
        session_config = self._build_session_config(instructions, tools)
        await self._websocket.send(
            json.dumps(session_config, ensure_ascii=False),
        )

    @abstractmethod
    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the session configuration message to initialize or update
        the realtime model session.

        Args:
            instructions (`str`):
                The instructions to guide the realtime model's behavior.
            tools (`list[dict]`, optional):
                The list of tools available to the realtime model.
            **kwargs (`Any`):
                Additional keyword arguments for session configuration.

        Returns:
            `dict`:
                The session configuration message.
        """

    async def disconnect(self) -> None:
        """Close the connection to the realtime model."""
        # TODO: session ended

        if self._incoming_task and not self._incoming_task.done():
            self._incoming_task.cancel()

        if self._websocket:
            await self._websocket.close()

    async def _receive_model_event_loop(self, outgoing_queue: Queue) -> None:
        """The loop to receive and handle the model responses.

        Args:
            outgoing_queue (`Queue`):
                The queue to push the model responses to the outside.
        """

        async for message in self._websocket:
            if isinstance(message, bytes):
                message = message.decode("utf-8")

            # Parse the message into ModelEvent instance(s)
            events = await self.parse_api_message(message)

            if events is None:
                continue

            if isinstance(events, ModelEvents.EventBase):
                events = [events]

            for event in events:
                # Send the event to the outgoing queue
                await outgoing_queue.put(event)

    @abstractmethod
    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse the message received from the realtime model API.

        Args:
            message (`str`):
                The message received from the realtime model API.

        Returns:
            `ModelEvents.EventBase | list[ModelEvents.EventBase] | None`:
                The unified model event(s) in agentscope format.
        """
