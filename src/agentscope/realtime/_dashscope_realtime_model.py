# -*- coding: utf-8 -*-
"""The dashscope realtime model class."""
import json
from typing import Literal, Any

import shortuuid

from ._events import ModelEvents
from ._base import RealtimeModelBase
from .._logging import logger
from .._utils._common import _get_bytes_from_web_url
from ..message import AudioBlock, TextBlock, ImageBlock, ToolResultBlock


class DashScopeRealtimeModel(RealtimeModelBase):
    """The DashScope realtime model class.

    TODO:
     - Support non-VAD mode
     - Support update session config during the session
    """

    support_input_modalities: list[str] = ["text", "audio", "image"]
    """The supported input modalities of the DashScope realtime model."""

    support_tools: bool = False
    """The DashScope Realtime API doesn't support tools yet (last updated in
    20260129)."""

    websocket_url: str = (
        "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model_name}"
    )
    """The websocket URL of the DashScope realtime model API."""

    websocket_headers: dict[str, str] = {
        "Authorization": "Bearer {api_key}",
        "X-DashScope-DataInspection": "disable",
    }
    """The websocket headers of the DashScope realtime model API."""

    input_sample_rate: int
    """The input audio sample rate."""

    output_sample_rate: int
    """The output audio sample rate."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        voice: str
        | Literal["Cherry", "Serena", "Ethan", "Chelsie"] = "Cherry",
        enable_input_audio_transcription: bool = True,
    ) -> None:
        """Initialize the DashScopeRealtimeModel class.

        Args:
            model_name (`str`):
                The model name, e.g. "qwen3-omni-flash-realtime".
            api_key (`str`):
                The API key for authentication.
            voice (`str | Literal["Cherry", "Serena", "Ethan", "Chelsie"]`, \
            defaults to `"Cherry"`):
                The voice to be used for text-to-speech.
            enable_input_audio_transcription (`bool`, defaults to `True`):
                Whether to enable input audio transcription.
        """
        super().__init__(model_name)

        self.voice = voice
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )

        # The dashscope realtime API requires 16kHz input sample rate
        # for all models.
        self.input_sample_rate = 16000

        # The output sample rate depends on the model.
        # For "qwen3-omni-flash-realtime" models, it's 24kHz.
        # For others, it's 16kHz.
        if model_name.startswith("qwen3-omni-flash-realtime"):
            self.output_sample_rate = 24000
        else:
            self.output_sample_rate = 16000

        # Set the model name in the websocket URL.
        self.websocket_url = self.websocket_url.format(model_name=model_name)

        # Set the API key in the websocket headers.
        self.websocket_headers["Authorization"] = self.websocket_headers[
            "Authorization"
        ].format(api_key=api_key)

        # Record the response ID for the current session.
        self._response_id = ""

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the session configuration."""
        session_config: dict = {
            "instructions": instructions,
            # The output modalities of the model
            "modalities": ["audio", "text"],
            "input_audio_format": "pcm" + str(self.input_sample_rate // 1000),
            "output_audio_format": "pcm"
            + str(self.output_sample_rate // 1000),
            "voice": self.voice,
            **kwargs,
        }

        # Input audio transcription
        if self.enable_input_audio_transcription:
            session_config["input_audio_transcription"] = {
                "model": "gummy-realtime-v1",
            }

        # By default, we enable the VAD capability
        # TODO: support none-VAD mode
        session_config["turn_detection"] = {
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 800,
        }

        return {
            "type": "session.update",
            "session": session_config,
        }

    async def send(
        self,
        data: AudioBlock | TextBlock | ImageBlock | ToolResultBlock,
    ) -> None:
        """Send the data to the DashScope realtime model for processing.

        .. note:: The DashScope Realtime API currently only supports audio and
        image data input.

        Args:
            data (`AudioBlock | TextBlock | ImageBlock | ToolResultBlock`):
                The data to be sent to the DashScope realtime model.
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
                "DashScope Realtime API does not support %s data input. "
                "Supported modalities are: %s",
                data_type,
                ", ".join(self.support_input_modalities),
            )
            return

        # Process the data based on its type
        if data_type == "image":
            to_send_message = await self._parse_image_data(
                ImageBlock(
                    type="image",
                    source=data.get("source"),
                ),
            )

        elif data_type == "audio":
            to_send_message = await self._parse_audio_data(
                AudioBlock(
                    type="audio",
                    source=data.get("source"),
                ),
            )

        elif data_type == "text":
            # TODO: The following code doesn't work and cannot support text
            #  input yet.
            to_send_message = json.dumps(
                {
                    "event_id": shortuuid.uuid(),
                    "type": "response.create",
                    "response": {
                        "instructions": data.get("text", ""),
                    },
                },
                ensure_ascii=False,
            )

        else:
            raise ValueError(
                f"Unsupported data type: {data_type}",
            )

        await self._websocket.send(to_send_message)

    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse the message received from the DashScope realtime model API.

        Args:
            message (`str`):
                The message received from the DashScope realtime model API.

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
                self._response_id = data.get("response", {}).get("id", "")
                model_event = ModelEvents.ModelResponseCreatedEvent(
                    response_id=self._response_id,
                )

            case "response.done":
                response = data.get("response", {})
                response_id = response.get("id", "") or self._response_id
                usage = response.get("usage", {})
                model_event = ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
                # clear the response id
                self._response_id = ""

            case "response.audio.delta":
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

            case "response.audio.done":
                model_event = ModelEvents.ModelResponseAudioDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ================ Transcription related events ================

            case "response.audio_transcript.delta":
                transcript_data = data.get("delta", "")
                if transcript_data:
                    model_event = (
                        ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                            response_id=self._response_id,
                            delta=transcript_data,
                            item_id=data.get("item_id", ""),
                        )
                    )

            case "response.audio_transcript.done":
                model_event = (
                    ModelEvents.ModelResponseAudioTranscriptDoneEvent(
                        response_id=self._response_id,
                        item_id=data.get("item_id", ""),
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
                    "Unknown DashScope realtime model event type: %s",
                    data.get("type", None),
                )

        return model_event

    async def _parse_image_data(self, block: ImageBlock) -> str:
        """Parse the image data block to the format required by the DashScope
        realtime model API.

        Args:
            block (`ImageBlock`):
                The image data block.

        Returns:
            `str`: The parsed message to be sent to the DashScope realtime
            model API.
        """
        if block["source"]["type"] == "base64":
            return json.dumps(
                {
                    "type": "input_image_buffer.append",
                    "image": block["source"]["data"],
                },
            )

        if block["source"]["type"] == "url":
            image = _get_bytes_from_web_url(block["source"]["url"])
            return json.dumps(
                {
                    "type": "input_image_url.append",
                    "image_url": image,
                },
            )

        raise ValueError(
            f"Unsupported image source type: {block['source']['type']}",
        )

    async def _parse_audio_data(self, block: AudioBlock) -> str:
        """Parse the audio data block to the format required by the DashScope
        realtime model API.

        Args:
            block (`AudioBlock`):
                The audio data block.

        Returns:
            `str`: The parsed message to be sent to the DashScope realtime
            model API.
        """
        source_type = block["source"]["type"]

        if source_type == "base64":
            audio_data = block["source"]["data"]

        elif source_type == "url":
            audio_data = _get_bytes_from_web_url(block["source"]["url"])

        else:
            raise ValueError(
                f"Unsupported audio source type: {source_type}",
            )

        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": audio_data,
            },
        )
