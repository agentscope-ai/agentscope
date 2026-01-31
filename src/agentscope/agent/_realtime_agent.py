# -*- coding: utf-8 -*-
"""The realtime agent class."""
import asyncio
import json
from asyncio import Queue

import shortuuid

from .._logging import logger
from ..message import (
    AudioBlock,
    Base64Source,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from ..module import StateModule
from ..realtime import (
    ModelEvents,
    RealtimeModelBase,
    ServerEvents,
    ClientEvents,
)
from ..tool import Toolkit


class RealtimeAgentBase(StateModule):
    """The realtime agent base class. Different from the `AgentBase` class,
    this class is designed for real-time interaction scenarios, such as
    realtime chat, voice assistants, etc.
    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: RealtimeModelBase,
        toolkit: Toolkit | None = None,
    ) -> None:
        """Initialize the RealtimeAgentBase class.

        Args:
            name (`str`):
                The name of the agent.
            sys_prompt (`str`):
                The system prompt of the agent.
            model (`RealtimeModelBase`):
                The realtime model used by the agent.
            toolkit (`Toolkit | None`, optional):
                A `Toolkit` object that contains the tool functions. If not
                provided, a default empty `Toolkit` will be created.
        """
        super().__init__()

        self.id = shortuuid.uuid()
        self.name = name
        self.sys_prompt = sys_prompt
        self.model = model
        self.toolkit = toolkit

        # Tool arguments accumulator for tracking tool call parameters
        self._tool_args_accumulator: dict[str, str] = {}

        # Tool name cache for storing tool names (since
        # ResponseToolUseDoneEvent doesn't have name)
        self._tool_name_cache: dict[str, str] = {}

        # A queue to handle the incoming events from other agents or the
        # frontend.
        self._incoming_queue = Queue()
        self._external_event_handling_task = None

        # The queue to gather model responses.
        self._model_response_queue = Queue()
        self._model_response_handling_task = None

    async def start(self, outgoing_queue: Queue) -> None:
        """Establish a connection for real-time interaction.

        Args:
            outgoing_queue (`Queue`):
                The queue to push messages to the frontend and other agents.
        """
        # Start the realtime model connection.
        await self.model.connect(
            self._model_response_queue,
            instructions=self.sys_prompt,
            tools=self.toolkit.get_json_schemas() if self.toolkit else None,
        )

        # Start the forwarding loop.
        self._external_event_handling_task = asyncio.create_task(
            self._forward_loop(),
        )

        # Start the response handling loop.
        self._model_response_handling_task = asyncio.create_task(
            self._model_response_loop(outgoing_queue),
        )

    async def stop(self) -> None:
        """Close the connection."""

        if not self._external_event_handling_task.done():
            self._external_event_handling_task.cancel()

        await self.model.disconnect()

    async def _forward_loop(self) -> None:
        """The loop to forward messages from other agents or the frontend to
        the realtime model for processing.

        outside ==> agent ==> realtime model
        """
        while True:
            event = await self._incoming_queue.get()

            match event:
                # TODO: handle both the server and client events, and send
                #  them to the realtime model as needed by the send method.
                # Only handle the events that we need
                case ServerEvents.AgentResponseAudioDeltaEvent() as event:
                    await self.model.send(
                        AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                media_type=event.format["type"],
                                data=event.delta,
                            ),
                        ),
                    )
                case ClientEvents.ClientAudioAppendEvent() as event:
                    await self.model.send(
                        AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                media_type=event.format["type"],
                                data=event.audio,
                            ),
                        ),
                    )

                case ClientEvents.ClientTextAppendEvent() as event:
                    await self.model.send(
                        TextBlock(
                            type="text",
                            text=event.text,
                        ),
                    )
                case ClientEvents.ClientImageAppendEvent() as event:
                    await self.model.send(
                        ImageBlock(
                            type="image",
                            source=Base64Source(
                                type="base64",
                                media_type=event.format["type"],
                                data=event.image,
                            ),
                        ),
                    )

    # pylint: disable=too-many-statements
    async def _model_response_loop(self, outgoing_queue: Queue) -> None:
        """The loop to handle model responses and forward them to the
        frontend and other agents.

        realtime model ==> agent ==> outside

        Args:
            outgoing_queue (`Queue`):
                The queue to push messages to the frontend and other agents.
        """
        while True:
            model_event = await self._model_response_queue.get()

            agent_kwargs = {"agent_id": self.id, "agent_name": self.name}

            agent_event = None
            match model_event:
                case ModelEvents.SessionCreatedEvent():
                    # Send the agent ready event to the outside.
                    agent_event = ServerEvents.AgentReadyEvent(**agent_kwargs)

                case ModelEvents.SessionEndedEvent():
                    # Send the agent session ended event to the outside.
                    agent_event = ServerEvents.AgentEndedEvent(**agent_kwargs)

                case ModelEvents.ResponseCreatedEvent() as event:
                    # The agent begins generating a response.
                    agent_event = ServerEvents.AgentResponseCreatedEvent(
                        response_id=event.response_id,
                        **agent_kwargs,
                    )

                case ModelEvents.ResponseDoneEvent() as event:
                    agent_event = ServerEvents.AgentResponseDoneEvent(
                        response_id=event.response_id,
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                        metadata=event.metadata,
                        **agent_kwargs,
                    )

                case ModelEvents.ResponseAudioDeltaEvent() as event:
                    agent_event = ServerEvents.AgentResponseAudioDeltaEvent(
                        response_id=event.response_id,
                        item_id=event.item_id,
                        delta=event.delta,
                        format=event.format,
                        **agent_kwargs,
                    )

                case ModelEvents.ResponseAudioDoneEvent() as event:
                    agent_event = ServerEvents.AgentResponseAudioDoneEvent(
                        response_id=event.response_id,
                        item_id=event.item_id,
                        **agent_kwargs,
                    )

                case ModelEvents.ResponseAudioTranscriptDeltaEvent() as event:
                    agent_event = (
                        ServerEvents.AgentResponseAudioTranscriptDeltaEvent(
                            response_id=event.response_id,
                            item_id=event.item_id,
                            delta=event.delta,
                            **agent_kwargs,
                        )
                    )

                case ModelEvents.ResponseAudioTranscriptDoneEvent() as event:
                    agent_event = (
                        ServerEvents.AgentResponseAudioTranscriptDoneEvent(
                            response_id=event.response_id,
                            item_id=event.item_id,
                            **agent_kwargs,
                        )
                    )

                case ModelEvents.ResponseToolUseDeltaEvent() as event:
                    agent_event = ServerEvents.AgentResponseToolUseDeltaEvent(
                        response_id=event.response_id,
                        item_id=event.item_id,
                        name=event.name,
                        call_id=event.call_id,
                        input=event.input,
                        **agent_kwargs,
                    )
                    # Store the accumulated arguments from model layer
                    # Note: The model layer already accumulates the arguments,
                    # so we just store the current accumulated value
                    self._tool_args_accumulator[event.call_id] = event.input
                    # Also cache the tool name since
                    # ResponseToolUseDoneEvent doesn't have it
                    self._tool_name_cache[event.call_id] = event.name

                case ModelEvents.ResponseToolUseDoneEvent() as event:
                    # Send the tool use done event immediately
                    done_event = ServerEvents.AgentResponseToolUseDoneEvent(
                        response_id=event.response_id,
                        item_id=event.item_id,
                        call_id=event.call_id,
                        **agent_kwargs,
                    )
                    await outgoing_queue.put(done_event)

                    # Then execute the tool call using accumulated arguments
                    if (
                        self.toolkit
                        and event.call_id in self._tool_args_accumulator
                    ):
                        try:
                            # Get the accumulated arguments and tool name
                            # from cache
                            accumulated_args = self._tool_args_accumulator[
                                event.call_id
                            ]
                            tool_name = self._tool_name_cache.get(
                                event.call_id,
                                "unknown",
                            )

                            # Create ToolUseBlock from the accumulated data
                            tool_use_block = ToolUseBlock(
                                type="tool_use",
                                id=event.call_id,
                                name=tool_name,
                                input=json.loads(accumulated_args)
                                if accumulated_args
                                else {},
                            )

                            # Execute the tool
                            tool_result = (
                                await self.toolkit.call_tool_function(
                                    tool_use_block,
                                )
                            )

                            # Process the tool result
                            final_output = None
                            async for chunk in tool_result:
                                if chunk.is_last:
                                    final_output = chunk.content
                                    break

                            # Send tool result back to model and to frontend
                            if final_output:
                                # Create ToolResultBlock to send back to
                                # the model
                                tool_result_block = ToolResultBlock(
                                    type="tool_result",
                                    id=event.call_id,
                                    name=tool_name,
                                    output=final_output,
                                )

                                # Send the tool result back to the model
                                await self.model.send(tool_result_block)

                                # Also send event to frontend/other agents
                                result_event = (
                                    ServerEvents.AgentResponseToolResultEvent(
                                        call_id=event.call_id,
                                        name=tool_name,
                                        output=final_output,
                                        **agent_kwargs,
                                    )
                                )
                                await outgoing_queue.put(result_event)

                            # Clear the accumulator and name cache for this
                            # call_id
                            del self._tool_args_accumulator[event.call_id]
                            if event.call_id in self._tool_name_cache:
                                del self._tool_name_cache[event.call_id]

                        except Exception as e:
                            logger.error("Error executing tool: %s", e)
                            # Get tool name from cache for error reporting
                            tool_name = self._tool_name_cache.get(
                                event.call_id,
                                "unknown",
                            )

                            # Send error result back to model
                            error_output = f"Error: {str(e)}"
                            error_tool_result_block = ToolResultBlock(
                                type="tool_result",
                                name=tool_name,
                                id=event.call_id,
                                output=error_output,
                            )
                            await self.model.send(error_tool_result_block)

                            # Also send error event to frontend/other agents
                            error_event = (
                                ServerEvents.AgentResponseToolResultEvent(
                                    call_id=event.call_id,
                                    name=tool_name,
                                    output=error_output,
                                    **agent_kwargs,
                                )
                            )
                            await outgoing_queue.put(error_event)

                            # Clear the accumulator and name cache even on
                            # error
                            if event.call_id in self._tool_args_accumulator:
                                del self._tool_args_accumulator[event.call_id]
                            if event.call_id in self._tool_name_cache:
                                del self._tool_name_cache[event.call_id]

                    # Don't assign to agent_event to avoid duplicate processing
                    agent_event = None

                case ModelEvents.InputTranscriptionDeltaEvent() as event:
                    agent_event = (
                        ServerEvents.AgentInputTranscriptionDeltaEvent(
                            delta=event.delta,
                            **agent_kwargs,
                        )
                    )

                case ModelEvents.InputTranscriptionDoneEvent() as event:
                    agent_event = (
                        ServerEvents.AgentInputTranscriptionDoneEvent(
                            transcript=event.transcript,
                            input_tokens=event.input_tokens or 0,
                            output_tokens=event.output_tokens or 0,
                            **agent_kwargs,
                        )
                    )

                case ModelEvents.InputStartedEvent():
                    agent_event = ServerEvents.AgentInputStartedEvent(
                        **agent_kwargs,
                    )

                case ModelEvents.InputDoneEvent():
                    agent_event = ServerEvents.AgentInputDoneEvent(
                        **agent_kwargs,
                    )

                case ModelEvents.ErrorEvent() as event:
                    agent_event = ServerEvents.AgentErrorEvent(
                        error_type=event.error_type,
                        code=event.code,
                        message=event.message,
                        **agent_kwargs,
                    )

                case _:
                    logger.debug(
                        "Unknown model event type: %s",
                        type(model_event),
                    )

            if agent_event is not None:
                # Put the processed response to the outgoing queue.
                await outgoing_queue.put(agent_event)

    async def handle_input(self, event: ClientEvents | ServerEvents) -> None:
        """Handle the input message from the frontend or the other agents."""
        await self._incoming_queue.put(event)
