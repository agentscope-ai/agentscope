# -*- coding: utf-8 -*-
"""The realtime agent class."""
import asyncio
from asyncio import Queue

import shortuuid

from .._logging import logger
from .._utils._common import _resample_pcm_delta
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


class RealtimeAgent(StateModule):
    """The realtime agent class. Different from the `AgentBase` class,
    this class is designed for real-time interaction scenarios, such as
    realtime chat, voice assistants, etc.

    Example:
        This realtime agent requires a queue to handle outgoing messages to
        the frontend and other agents, and its lifecycle is managed by the
        `start` and `stop` methods.

        .. code-block:: python
            :caption: An example of using the RealtimeAgent class.

            from agentscope.agent import RealtimeAgent
            from agentscope.realtime import DashScopeRealtimeModel
            import asyncio

            agent = RealtimeAgent(
                name="Friday",
                sys_prompt="You are a helpful assistant.",
                model=DashScopeRealtimeModel(
                    model_name="qwen3-omni-flash-realtime",
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
            )

            queue = asyncio.Queue()
            await agent.start(queue)

            # handle the outgoing messages from the agent in another asyncio
            # task
            ...

            await agent.stop()

    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: RealtimeModelBase,
        toolkit: Toolkit | None = None,
    ) -> None:
        """Initialize the RealtimeAgent class.

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
        logger.info(
            "Agent '%s' begins the loops to receive external events",
            self.name,
        )

        while True:
            event = await self._incoming_queue.get()

            match event:
                # Only handle the events that we need
                case ServerEvents.AgentResponseAudioDeltaEvent() as event:
                    # Convert the sample rate to the required format by the
                    # model
                    receive_rate = event.format.rate
                    if self.model.input_sample_rate != receive_rate:
                        delta = _resample_pcm_delta(
                            event.delta,
                            receive_rate,
                            self.model.input_sample_rate,
                        )

                    else:
                        delta = event.delta

                    await self.model.send(
                        AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                media_type=event.format.type,
                                data=delta,
                            ),
                        ),
                    )

                case ServerEvents.AgentResponseAudioDoneEvent():
                    # Send a silence audio block to indicate the end of audio
                    pass

                case ClientEvents.ClientAudioAppendEvent() as event:
                    # Construct media_type from format info
                    # format contains: {"sample_rate": 16000, "encoding":
                    # "pcm16"}
                    # encoding = event.format.get("encoding", "pcm16")
                    # media_type = (
                    #     f"audio/{encoding.replace('16', '')}"
                    #     if "pcm" in encoding
                    #     else "audio/pcm"
                    # )

                    await self.model.send(
                        AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                media_type=event.format.type,
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
                    # Construct media_type from format info
                    media_type = event.format.get("type", "image/jpeg")

                    await self.model.send(
                        ImageBlock(
                            type="image",
                            source=Base64Source(
                                type="base64",
                                media_type=media_type,
                                data=event.image,
                            ),
                        ),
                    )

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
                # The events that can be converted from model events to agent
                #  events directly
                case (
                    ModelEvents.ModelResponseCreatedEvent()
                    | ModelEvents.ModelResponseDoneEvent()
                    | ModelEvents.ModelResponseAudioDeltaEvent()
                    | ModelEvents.ModelResponseAudioDoneEvent()
                    | ModelEvents.ModelResponseAudioTranscriptDeltaEvent()
                    | ModelEvents.ModelResponseAudioTranscriptDoneEvent()
                    | ModelEvents.ModelResponseToolUseDeltaEvent()
                    | ModelEvents.ModelInputTranscriptionDeltaEvent()
                    | ModelEvents.ModelInputTranscriptionDoneEvent()
                    | ModelEvents.ModelInputStartedEvent()
                    | ModelEvents.ModelInputDoneEvent()
                    | ModelEvents.ModelErrorEvent()
                ) as event:
                    # Directly map the model event to agent event
                    agent_event = ServerEvents.from_model_event(
                        event,
                        **agent_kwargs,
                    )

                # The events that need special handling
                case ModelEvents.ModelSessionCreatedEvent():
                    # Send the agent ready event to the outside.
                    agent_event = ServerEvents.AgentReadyEvent(**agent_kwargs)

                case ModelEvents.ModelSessionEndedEvent():
                    # Send the agent session ended event to the outside.
                    agent_event = ServerEvents.AgentEndedEvent(**agent_kwargs)

                # The tool use done that requires executing the tool
                # Such event may generate multiple outgoing events:
                # 1. Tool use done event
                # 2. Tool result event
                case ModelEvents.ModelResponseToolUseDoneEvent() as event:
                    # Send the tool use done event immediately
                    done_event = ServerEvents.AgentResponseToolUseDoneEvent(
                        response_id=event.response_id,
                        item_id=event.item_id,
                        tool_use=event.tool_use,
                        **agent_kwargs,
                    )

                    # Directly put the done event to the outgoing queue
                    await outgoing_queue.put(done_event)

                    # Then execute the tool call using accumulated arguments
                    if self.toolkit:
                        # Execute the tool call asynchronously
                        asyncio.create_task(
                            self._acting(
                                tool_use=event.tool_use,
                                outgoing_queue=outgoing_queue,
                            ),
                        )

                case _:
                    logger.debug(
                        "Unknown model event type: %s",
                        type(model_event),
                    )

            if agent_event is not None:
                # Put the processed response to the outgoing queue.
                await outgoing_queue.put(agent_event)

    async def handle_input(
        self,
        event: ClientEvents.EventBase | ServerEvents.EventBase,
    ) -> None:
        """Handle the input message from the frontend or the other agents.

        Args:
            event (`ClientEvents.EventBase | ServerEvents.EventBase`):
                The input event from the frontend or other agents.
        """
        await self._incoming_queue.put(event)

    async def _acting(
        self,
        tool_use: ToolUseBlock,
        outgoing_queue: Queue,
    ) -> None:
        """Execute the tool call and send the result back to the outside (
        frontend or other agents).

        Args:
            tool_use (`ToolUseBlock`):
                The tool use block containing the tool call information.
            outgoing_queue (`Queue`):
                The queue to push messages to the frontend and other agents.
        """
        if not self.toolkit:
            return

        res = await self.toolkit.call_tool_function(tool_use)

        last_chunk = None
        async for chunk in res:
            last_chunk = chunk

        if last_chunk:
            tool_result_block = ToolResultBlock(
                type="tool_result",
                id=tool_use.get("id"),
                name=tool_use.get("name"),
                output=last_chunk.content,
            )

            # Send the tool result back to the model
            await self.model.send(tool_result_block)

            # Also send event to frontend/other agents
            outgoing_event = ServerEvents.AgentResponseToolResultEvent(
                tool_result=tool_result_block,
                agent_id=self.id,
                agent_name=self.name,
            )

            await outgoing_queue.put(outgoing_event)
