# -*- coding: utf-8 -*-
"""The OpenAI realtime model class."""
import json
from typing import Literal, Any

from ._events import ModelEvents
from ._base import RealtimeModelBase
from .._logging import logger
from .._utils._common import _get_bytes_from_web_url, _json_loads_with_repair
from ..message import (
    AudioBlock,
    TextBlock,
    ImageBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class OpenAIRealtimeModel(RealtimeModelBase):
    """The OpenAI realtime model class."""

    support_input_modalities: list[str] = ["audio", "text"]
    """The supported input modalities of the OpenAI realtime model."""

    support_tools: bool = True
    """The OpenAI realtime model supports tools API."""

    websocket_url: str = "wss://api.openai.com/v1/realtime?model={model_name}"
    """The websocket URL of the OpenAI realtime model API."""

    websocket_headers: dict[str, str] = {
        "Authorization": "Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }
    """The websocket headers of the OpenAI realtime model API."""

    input_sample_rate: int
    """The input audio sample rate."""

    output_sample_rate: int
    """The output audio sample rate."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        voice: Literal["alloy", "echo", "marin", "cedar"] | str = "alloy",
        enable_input_audio_transcription: bool = True,
    ) -> None:
        """Initialize the OpenAIRealtimeModel class.

        Args:
            model_name (`str`):
                The model name, e.g. "gpt-4o-realtime-preview".
            api_key (`str`):
                The API key for authentication.
            voice (`Literal["alloy", "echo", "marin", "cedar"] | str`, \
            defaults to `"alloy"`):
                The voice to be used for text-to-speech.
            enable_input_audio_transcription (`bool`, defaults to `True`):
                Whether to enable input audio transcription.
        """
        super().__init__(model_name)

        self.voice = voice
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )

        # The OpenAI realtime API uses 24kHz for both input and output.
        self.input_sample_rate = 24000
        self.output_sample_rate = 24000

        # Set the model name in the websocket URL.
        self.websocket_url = self.websocket_url.format(model_name=model_name)

        # Set the API key in the websocket headers.
        self.websocket_headers["Authorization"] = self.websocket_headers[
            "Authorization"
        ].format(api_key=api_key)

        # Record the response ID for the current session.
        self._response_id = ""

        # Tool arguments accumulator for tracking tool call parameters
        self._tool_args_accumulator: dict[str, str] = {}

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the session configuration for the OpenAI realtime model."""

        session_config: dict[str, Any] = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                    },
                },
                "output": {
                    "voice": self.voice,
                },
            },
            "instructions": instructions,
            **kwargs,
        }

        # Input audio transcription
        if self.enable_input_audio_transcription:
            session_config["audio"]["input"]["transcription"] = {
                "model": "whisper-1",
            }

        # Tools configuration
        if tools:
            # TODO: format the tools
            session_config["tools"] = tools

        return {
            "type": "session.update",
            "session": session_config,
        }

    async def send(
        self,
        data: AudioBlock | TextBlock | ImageBlock | ToolResultBlock,
    ) -> None:
        """Send the data to the OpenAI realtime model for processing.

        Args:
            data (`AudioBlock | TextBlock | ImageBlock | ToolResultBlock`):
                The data to be sent to the OpenAI realtime model.
        """
        from websockets import State

        if not self._websocket or self._websocket.state != State.OPEN:
            raise RuntimeError(
                f"WebSocket is not connected for model {self.model_name}. "
                "Call the `connect` method first.",
            )

        # Type checking
        assert (
            isinstance(data, dict) and "type" in data
        ), "Data must be a dict with a 'type' field."

        # The source must be base64 for audio data
        data_type = data.get("type")

        if data_type not in self.support_input_modalities:
            logger.warning(
                "OpenAI Realtime API does not support %s data input. "
                "Supported modalities are: %s",
                data_type,
                ", ".join(self.support_input_modalities),
            )
            return

        # Process the data based on its type
        if data_type == "audio":
            to_send_message = await self._parse_audio_data(
                AudioBlock(
                    type="audio",
                    source=data.get("source"),
                ),
            )

        elif data_type == "text":
            to_send_message = await self._parse_text_data(
                TextBlock(
                    type="text",
                    text=data.get("text"),
                ),
            )

        elif data_type == "tool_result":
            to_send_message = await self._parse_tool_result_data(
                ToolResultBlock(
                    type="tool_result",
                    id=data.get("id"),
                    output=data.get("output"),
                    name=data.get("name"),
                ),
            )

        else:
            raise RuntimeError(f"Unsupported data type {data_type}")

        await self._websocket.send(to_send_message)

    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse the message received from the OpenAI realtime model API.

        Args:
            message (`str`):
                The message received from the OpenAI realtime model API.

        Returns:
            `ModelEvents.EventBase | list[ModelEvents.EventBase] | None`:
                The unified model event(s) in agentscope format.
        """
        try:
            data = json.loads(message)
        except json.decoder.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        model_event = None
        match data.get("type", ""):
            # ================ Session related events ================
            case "session.created":
                model_event = ModelEvents.ModelSessionCreatedEvent(
                    session_id=data.get("session", {}).get("id", ""),
                )

            case "session.updated":
                # TODO: handle the session updated event
                pass

            # ================ Response related events ================
            case "response.created":
                # TODO: @qbc we create one shortuuid here?
                self._response_id = data.get("response", {}).get("id", "")
                model_event = ModelEvents.ModelResponseCreatedEvent(
                    response_id=self._response_id,
                )

            case "response.done":
                response = data.get("response", {})
                response_id = response.get("id", self._response_id)
                usage = response.get("usage", {})
                model_event = ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
                # clear the response id
                self._response_id = ""

            case "response.output_audio.delta":
                audio_data = data.get("delta", "")
                if audio_data:
                    model_event = ModelEvents.ModelResponseAudioDeltaEvent(
                        response_id=self._response_id,
                        item_id=data.get("item_id", ""),
                        delta=audio_data,
                        format={
                            "type": "audio/pcm",
                            "rate": self.output_sample_rate,
                        },
                    )

            case "response.output_audio.done":
                model_event = ModelEvents.ModelResponseAudioDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ================ Transcription related events ================
            case "response.output_audio_transcript.delta":
                transcript_data = data.get("delta", "")
                if transcript_data:
                    model_event = (
                        ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                            response_id=self._response_id,
                            delta=transcript_data,
                            item_id=data.get("item_id", ""),
                        )
                    )

            case "response.output_audio_transcript.done":
                model_event = (
                    ModelEvents.ModelResponseAudioTranscriptDoneEvent(
                        response_id=self._response_id,
                        item_id=data.get("item_id", ""),
                    )
                )

            case "response.function_call_arguments.delta":
                arguments_delta = data.get("delta")
                call_id = data.get("call_id", "")
                if arguments_delta:
                    # Accumulate arguments
                    if call_id not in self._tool_args_accumulator:
                        self._tool_args_accumulator[call_id] = ""
                    self._tool_args_accumulator[call_id] += arguments_delta

                    # Return the accumulated arguments instead of just the
                    # delta
                    model_event = ModelEvents.ModelResponseToolUseDeltaEvent(
                        response_id=self._response_id,
                        item_id=data.get("item_id", ""),
                        tool_use=ToolUseBlock(
                            type="tool_use",
                            id=call_id,
                            name=data.get("name", ""),
                            input={},
                            raw_input=self._tool_args_accumulator[call_id],
                        ),
                    )
                    # TODO: This handles only one tool call at a time. For
                    #  parallel tool calls, we might need to reconsider the
                    #  event handling mechanism.

            case "response.function_call_arguments.done":
                call_id = data.get("call_id", "")
                current_input = self._tool_args_accumulator[call_id]
                model_event = ModelEvents.ModelResponseToolUseDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    tool_use=ToolUseBlock(
                        type="tool_use",
                        id=call_id,
                        name=data.get("name", ""),
                        input=_json_loads_with_repair(current_input),
                        raw_input=current_input,
                    ),
                )
                # Clear the accumulator for this call_id when done
                if call_id in self._tool_args_accumulator:
                    del self._tool_args_accumulator[call_id]

            case "conversation.item.input_audio_transcription.delta":
                delta = data.get("delta", "")
                if delta:
                    model_event = (
                        ModelEvents.ModelInputTranscriptionDeltaEvent(
                            item_id=data.get("item_id", ""),
                            delta=delta,
                        )
                    )

            case "conversation.item.input_audio_transcription.completed":
                transcript_data = data.get("transcript", "")
                if transcript_data:
                    model_event = ModelEvents.ModelInputTranscriptionDoneEvent(
                        transcript=transcript_data,
                        item_id=data.get("item_id", ""),
                    )

            # ================= VAD related events =================
            case "input_audio_buffer.speech_started":
                model_event = ModelEvents.ModelInputStartedEvent(
                    item_id=data.get("item_id", ""),
                    audio_start_ms=data.get("audio_start_ms", 0),
                )

            case "input_audio_buffer.speech_stopped":
                model_event = ModelEvents.ModelInputDoneEvent(
                    item_id=data.get("item_id", ""),
                    audio_end_ms=data.get("audio_end_ms", 0),
                )

            # ================= Error events =================
            case "error":
                error = data.get("error", {})
                model_event = ModelEvents.ModelErrorEvent(
                    error_type=error.get("type", "unknown"),
                    code=error.get("code", "unknown"),
                    message=error.get("message", "An unknown error occurred."),
                )

            # ================= Unknown events =================
            case _:
                logger.debug(
                    "Unknown OpenAI realtime model event type: %s",
                    data.get("type", None),
                )

        return model_event

    async def _parse_audio_data(self, block: AudioBlock) -> str:
        """Parse the audio data block to the format required by the OpenAI
        realtime model API.

        Args:
            block (`AudioBlock`):
                The audio data block.

        Returns:
            `str`: The parsed message to be sent to the OpenAI realtime
            model API.
        """
        if block["source"]["type"] == "base64":
            audio_data = block["source"]["data"]

        elif block["source"]["type"] == "url":
            audio_data = _get_bytes_from_web_url(block["source"]["url"])

        else:
            raise ValueError(
                f"Unsupported audio source type: {block['source']['type']}",
            )

        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": audio_data,
            },
        )

    async def _parse_text_data(self, block: TextBlock) -> str:
        """Parse the text data block to the format required by the OpenAI
        realtime model API.

        Args:
            block (`TextBlock`):
                The text data block.

        Returns:
            `str`: The parsed message to be sent to the OpenAI realtime
            model API.
        """
        text = block.get("text", "")

        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                        },
                    ],
                },
            },
        )

    async def _parse_tool_result_data(self, block: ToolResultBlock) -> str:
        """Parse the tool result data block to the format required by the
        OpenAI realtime model API.

        Args:
            block (`ToolResultBlock`):
                The tool result data block.

        Returns:
            `str`: The parsed message to be sent to the OpenAI realtime
            model API.
        """
        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": block.get("id"),
                    "output": block.get("output"),
                },
            },
        )
