# -*- coding: utf-8 -*-
# pylint: disable=too-many-return-statements, too-many-branches
"""Callback-based Voice Agent with incoming queue.

This agent:
- Has an incoming_queue to receive AgentEvents from other agents
- Registers a callback with Model to receive ModelEvents
- Converts ModelEvents to AgentEvents and pushes to MsgStream queue
- Processes incoming AgentEvents and sends audio to model
- Supports toolkit for tool calling
- Supports memory for conversation history
"""

import asyncio
import json

import shortuuid

from ..._logging import logger
from ...memory import MemoryBase, InMemoryMemory
from ...message import Msg
from ...message import TextBlock as MsgTextBlock
from ...message import ToolUseBlock as MsgToolUseBlock
from ...message import ToolResultBlock as MsgToolResultBlock
from ...module import StateModule
from ...tool import Toolkit

from .events import (
    ModelEvent,
    ModelEventType,
    ModelSessionCreated,
    ModelSessionUpdated,
    ModelResponseCreated,
    ModelResponseAudioDelta,
    ModelResponseAudioTranscriptDelta,
    ModelResponseToolUseDelta,
    ModelResponseToolUseDone,
    ModelResponseDone,
    ModelInputTranscriptionDelta,
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
    AgentEvent,
    AgentSessionCreated,
    AgentSessionUpdated,
    AgentResponseCreated,
    AgentResponseDelta,
    AgentResponseDone,
    AgentInputTranscriptionDelta,
    AgentInputTranscriptionDone,
    AgentInputStarted,
    AgentInputDone,
    AgentError,
    TextBlock,
    AudioBlock,
    ToolUseBlock,
)
from .model import RealtimeVoiceModelBase


class RealtimeVoiceAgent(StateModule):
    """Voice agent using callback pattern for event handling.

    This agent:
    1. Receives ModelEvents from Model via callback
    2. Converts ModelEvents to AgentEvents
    3. Pushes AgentEvents to shared MsgStream queue
    4. Receives AgentEvents from other agents via incoming_queue
    5. Extracts audio from AgentEvents and sends to model

    Architecture:
        Model -> (callback) -> Agent -> (queue_stream) -> MsgStream
                                 ^
                                 |
        incoming_queue <-------- MsgStream dispatch_loop

    Example:
        ```python
        model = DashScopeRealtimeModel(api_key="xxx")
        agent = RealtimeVoiceAgent(
            name="assistant",
            model=model,
        )

        msg_stream = EventMsgStream(agents=[agent])
        await msg_stream.start()
        ```
    """

    id: str
    """Unique identifier for the agent."""

    def __init__(
        self,
        name: str,
        model: RealtimeVoiceModelBase,
        sys_prompt: str = "You are a helpful assistant.",
        toolkit: Toolkit | None = None,
        memory: MemoryBase | None = None,
    ) -> None:
        """Initialize the callback voice agent.

        Args:
            name: Agent name.
            model: RealtimeVoiceModelBase instance.
            sys_prompt: System prompt for the agent.
            toolkit: Optional toolkit for tool calling.
            memory: Optional memory for conversation history.
        """
        super().__init__()

        self.id = shortuuid.uuid()
        self.name = name
        self.model = model
        self.sys_prompt = sys_prompt
        self.toolkit = toolkit or Toolkit()
        self.memory = memory or InMemoryMemory()

        # Queue for receiving AgentEvents from other agents (via MsgStream)
        self.incoming_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

        # Reference to MsgStream's central queue (set by MsgStream.start)
        self._queue_stream: asyncio.Queue[AgentEvent] | None = None

        # State
        self._initialized = False
        self._stop_event = asyncio.Event()
        self._incoming_task: asyncio.Task | None = None

        # Current response tracking
        self._current_response_id: str | None = None
        self._current_session_id: str | None = None

        # Response text accumulator (for memory)
        self._response_text: str = ""

        # Tool call accumulator (for streaming tool calls)
        self._tool_calls: dict[str, dict] = {}  # call_id -> {name, arguments}

        # Track the speaker of incoming audio (for input transcription)
        self._current_incoming_speaker: str | None = None

    async def start(
        self,
        msgstream_queue: asyncio.Queue[AgentEvent],
    ) -> None:
        """Start the agent and connect to model.

        This method:
        1. Sets the MsgStream queue reference
        2. Registers callback with model
        3. Starts the model connection with toolkit
        4. Starts the incoming event processing loop

        Args:
            msgstream_queue: The central queue from MsgStream.
        """
        if self._initialized:
            return

        self._queue_stream = msgstream_queue
        self._stop_event.clear()

        # Register callback with model
        self.model.agent_callback = self._on_model_event

        # Get tools schema from toolkit
        tools = None
        if self.toolkit:
            tools = self.toolkit.get_json_schemas()

        # Start model (connects WebSocket)
        await self.model.start(instructions=self.sys_prompt, tools=tools)

        # Start incoming event processing loop
        self._incoming_task = asyncio.create_task(
            self._process_incoming_loop(),
        )

        self._initialized = True
        logger.info("RealtimeVoiceAgent %s started", self.name)

    def _on_model_event(self, model_event: ModelEvent) -> None:
        """Callback for ModelEvents from model.

        Converts ModelEvent to AgentEvent and pushes to MsgStream queue.

        Args:
            model_event: The ModelEvent from model.
        """
        logger.debug(
            "Agent %s received ModelEvent: %s",
            self.name,
            model_event.type,
        )

        # Convert ModelEvent to AgentEvent
        agent_event = self._convert_model_to_agent_event(model_event)

        if agent_event and self._queue_stream:
            # Push to MsgStream queue (non-blocking)
            try:
                self._queue_stream.put_nowait(agent_event)
            except asyncio.QueueFull:
                logger.warning(
                    "MsgStream queue full, dropping event: %s",
                    agent_event.type,
                )

    def _convert_model_to_agent_event(
        self,
        model_event: ModelEvent,
    ) -> AgentEvent | None:
        """Convert ModelEvent to AgentEvent.

        Args:
            model_event: The ModelEvent to convert.

        Returns:
            Converted AgentEvent, or None if event should be ignored.
        """
        event_type = model_event.type

        # Session events
        if event_type == ModelEventType.SESSION_CREATED:
            assert isinstance(model_event, ModelSessionCreated)
            self._current_session_id = model_event.session_id
            return AgentSessionCreated(
                agent_id=self.id,
                agent_name=self.name,
                session_id=model_event.session_id,
            )

        elif event_type == ModelEventType.SESSION_UPDATED:
            assert isinstance(model_event, ModelSessionUpdated)
            return AgentSessionUpdated(
                agent_id=self.id,
                agent_name=self.name,
                session_id=model_event.session_id,
            )

        # Response events
        elif event_type == ModelEventType.RESPONSE_CREATED:
            assert isinstance(model_event, ModelResponseCreated)
            self._current_response_id = model_event.response_id
            return AgentResponseCreated(
                agent_id=self.id,
                agent_name=self.name,
                response_id=model_event.response_id,
            )

        elif event_type == ModelEventType.RESPONSE_AUDIO_DELTA:
            assert isinstance(model_event, ModelResponseAudioDelta)
            return AgentResponseDelta(
                agent_id=self.id,
                agent_name=self.name,
                response_id=model_event.response_id,
                delta=AudioBlock(
                    data=model_event.delta,
                    media_type="audio/pcm;rate=24000",
                ),
            )

        elif event_type == ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            assert isinstance(model_event, ModelResponseAudioTranscriptDelta)
            # Accumulate response text for memory
            self._response_text += model_event.delta
            return AgentResponseDelta(
                agent_id=self.id,
                agent_name=self.name,
                response_id=model_event.response_id,
                delta=TextBlock(text=model_event.delta),
            )

        elif event_type == ModelEventType.RESPONSE_DONE:
            assert isinstance(model_event, ModelResponseDone)
            self._current_response_id = None
            # Save response to memory
            asyncio.create_task(self._save_response_to_memory())
            return AgentResponseDone(
                agent_id=self.id,
                agent_name=self.name,
                response_id=model_event.response_id,
            )

        # Tool use events
        elif event_type == ModelEventType.RESPONSE_TOOL_USE_DELTA:
            assert isinstance(model_event, ModelResponseToolUseDelta)
            call_id = model_event.call_id
            # Accumulate tool call arguments
            if call_id not in self._tool_calls:
                self._tool_calls[call_id] = {
                    "name": model_event.name or "",
                    "arguments": "",
                }
            self._tool_calls[call_id]["arguments"] += model_event.delta
            if model_event.name:
                self._tool_calls[call_id]["name"] = model_event.name
            # Don't emit event for delta, wait for done
            return None

        elif event_type == ModelEventType.RESPONSE_TOOL_USE_DONE:
            assert isinstance(model_event, ModelResponseToolUseDone)
            call_id = model_event.call_id
            tool_call = self._tool_calls.pop(call_id, None)
            if tool_call:
                # Execute tool asynchronously
                asyncio.create_task(
                    self._execute_tool(
                        call_id,
                        tool_call["name"],
                        tool_call["arguments"],
                    ),
                )
                # Emit tool use event
                try:
                    arguments = json.loads(tool_call["arguments"])
                except json.JSONDecodeError:
                    arguments = {"raw": tool_call["arguments"]}
                return AgentResponseDelta(
                    agent_id=self.id,
                    agent_name=self.name,
                    response_id=model_event.response_id,
                    delta=ToolUseBlock(
                        id=call_id,
                        name=tool_call["name"],
                        input=arguments,
                    ),
                )
            return None

        # Input transcription events
        elif event_type == ModelEventType.INPUT_TRANSCRIPTION_DELTA:
            assert isinstance(model_event, ModelInputTranscriptionDelta)
            return AgentInputTranscriptionDelta(
                agent_id=self.id,
                agent_name=self.name,
                delta=model_event.delta,
                item_id=model_event.item_id or "",
                content_index=model_event.content_index or 0,
            )

        elif event_type == ModelEventType.INPUT_TRANSCRIPTION_DONE:
            assert isinstance(model_event, ModelInputTranscriptionDone)
            # Save input transcription to memory
            # Use tracked speaker name if available, otherwise "user"
            speaker_name = self._current_incoming_speaker or "user"
            asyncio.create_task(
                self._save_input_transcription_to_memory(
                    speaker_name,
                    model_event.transcript,
                ),
            )
            # Reset speaker tracking
            self._current_incoming_speaker = None
            return AgentInputTranscriptionDone(
                agent_id=self.id,
                agent_name=self.name,
                transcription=model_event.transcript,
                item_id=model_event.item_id or "",
            )

        # Input detection events
        elif event_type == ModelEventType.INPUT_STARTED:
            assert isinstance(model_event, ModelInputStarted)
            return AgentInputStarted(
                agent_id=self.id,
                agent_name=self.name,
                item_id=model_event.item_id,
                audio_start_ms=model_event.audio_start_ms,
            )

        elif event_type == ModelEventType.INPUT_DONE:
            assert isinstance(model_event, ModelInputDone)
            return AgentInputDone(
                agent_id=self.id,
                agent_name=self.name,
                item_id=model_event.item_id,
                audio_end_ms=model_event.audio_end_ms,
            )

        # Error events
        elif event_type == ModelEventType.ERROR:
            assert isinstance(model_event, ModelError)
            return AgentError(
                agent_id=self.id,
                agent_name=self.name,
                error_type=model_event.error_type,
                code=model_event.code,
                message=model_event.message,
            )

        # Ignore other events (WEBSOCKET_CONNECT, etc.)
        return None

    async def _process_incoming_loop(self) -> None:
        """Process incoming AgentEvents from other agents.

        This loop:
        1. Gets events from incoming_queue
        2. Filters out events from self
        3. Extracts audio and sends to model
        """
        logger.info("Agent %s: Incoming processing loop started", self.name)

        try:
            while not self._stop_event.is_set():
                try:
                    # Wait for incoming event with timeout
                    event = await asyncio.wait_for(
                        self.incoming_queue.get(),
                        timeout=0.1,
                    )

                    # Process the event
                    await self._handle_incoming_event(event)

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        except Exception as e:
            logger.error("Agent %s incoming loop error: %s", self.name, e)
        finally:
            logger.info("Agent %s: Incoming processing loop ended", self.name)

    async def _handle_incoming_event(self, event: AgentEvent) -> None:
        """Handle an incoming AgentEvent.

        Args:
            event: The AgentEvent to handle.
        """
        # Skip events from self
        if event.agent_id == self.id:
            return

        logger.debug(
            "Agent %s handling incoming event from %s: %s",
            self.name,
            event.agent_name,
            event.type,
        )

        # Handle audio delta - send to model
        if isinstance(event, AgentResponseDelta):
            if isinstance(event.delta, AudioBlock):
                # Track the speaker for input transcription
                self._current_incoming_speaker = event.agent_name
                # Decode and send audio to model
                import base64

                audio_bytes = base64.b64decode(event.delta.data)
                self.model.send_audio(audio_bytes)

    def stop(self) -> None:
        """Stop the agent."""
        self._stop_event.set()

    async def close(self) -> None:
        """Close the agent and release resources."""
        self.stop()

        # Cancel incoming task
        if self._incoming_task and not self._incoming_task.done():
            self._incoming_task.cancel()
            try:
                await self._incoming_task
            except asyncio.CancelledError:
                pass

        # Clear pending tool calls
        self._tool_calls.clear()

        # Close model
        if self._initialized:
            await self.model.close()
            self._initialized = False

        logger.info("RealtimeVoiceAgent %s closed", self.name)

    @property
    def is_running(self) -> bool:
        """Check if the agent is running."""
        return self._initialized and not self._stop_event.is_set()

    # =========================================================================
    # Memory Operations
    # =========================================================================

    async def _save_response_to_memory(self) -> None:
        """Save the accumulated response text to memory."""
        if self._response_text:
            assistant_msg = Msg(
                name=self.name,
                content=[MsgTextBlock(type="text", text=self._response_text)],
                role="assistant",
            )
            await self.memory.add(assistant_msg)
            logger.debug(
                "Saved response to memory: %d chars",
                len(self._response_text),
            )
            # Reset accumulator
            self._response_text = ""

    async def _save_input_transcription_to_memory(
        self,
        speaker_name: str,
        text: str,
    ) -> None:
        """Save input transcription to memory.

        Args:
            speaker_name: Name of the speaker (other agent name or "user").
            text: The transcribed speech.
        """
        if not text:
            return

        # Use "user" role for external input
        input_msg = Msg(
            name=speaker_name,
            content=[MsgTextBlock(type="text", text=text)],
            role="user",
        )
        await self.memory.add(input_msg)
        logger.debug(
            "Saved input transcription to memory: %s said '%s'",
            speaker_name,
            text[:50],
        )

    # =========================================================================
    # Tool Execution
    # =========================================================================

    async def _execute_tool(
        self,
        tool_id: str,
        tool_name: str,
        arguments_json: str,
    ) -> None:
        """Execute a tool and send the result back to the model.

        Args:
            tool_id: The tool call ID.
            tool_name: The name of the tool.
            arguments_json: The tool arguments as JSON string.
        """
        if not self.toolkit:
            logger.warning(
                "Tool call received but no toolkit: %s",
                tool_name,
            )
            return

        logger.info("Executing tool: %s(%s)", tool_name, tool_id)

        try:
            # Parse arguments
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError:
                arguments = {}

            # Create tool use block for toolkit
            tool_use_block = ToolUseBlock(
                id=tool_id,
                name=tool_name,
                input=arguments,
            )

            # Save tool call to memory
            tool_call_msg = Msg(
                name=self.name,
                content=[
                    MsgToolUseBlock(
                        type="tool_use",
                        id=tool_id,
                        name=tool_name,
                        input=arguments,
                    ),
                ],
                role="assistant",
            )
            await self.memory.add(tool_call_msg)

            # Execute tool
            result = await self.toolkit.call_tool_function(tool_use_block)

            # Convert result to string
            if isinstance(result, (dict, list)):
                result_str = json.dumps(result, ensure_ascii=False)
            else:
                result_str = str(result)

            # Save tool result to memory
            tool_result_msg = Msg(
                name="system",
                content=[
                    MsgToolResultBlock(
                        type="tool_result",
                        id=tool_id,
                        name=tool_name,
                        output=[MsgTextBlock(type="text", text=result_str)],
                    ),
                ],
                role="system",
            )
            await self.memory.add(tool_result_msg)

            # Send result back to model
            await self.model.send_tool_result(tool_id, tool_name, result_str)
            logger.info("Tool %s executed successfully", tool_name)

        except Exception as e:
            logger.error("Tool execution error: %s", e)
            # Send error result back to model
            error_result = json.dumps({"error": str(e)})
            await self.model.send_tool_result(tool_id, tool_name, error_result)
