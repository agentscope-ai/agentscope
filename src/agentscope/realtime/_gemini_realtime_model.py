# -*- coding: utf-8 -*-
"""The Gemini realtime model class."""
import json
from typing import Literal, Any

import shortuuid

from ._events import ModelEvents
from ._base import RealtimeModelBase
from .._logging import logger
from .._utils._common import _get_bytes_from_web_url
from ..message import (
    AudioBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class GeminiRealtimeModel(RealtimeModelBase):
    """The Gemini realtime model class."""

    support_input_modalities: list[str] = [
        "audio",
        "text",
        "image",
        "tool_result",
    ]
    """The supported input modalities of the Gemini realtime model."""

    websocket_url: str = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1alpha.GenerativeService."
        "BidiGenerateContent?key="
    )
    """The websocket URL of the Gemini realtime model API."""

    websocket_headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    """The websocket headers of the Gemini realtime model API."""

    input_sample_rate: int
    """The input audio sample rate."""

    output_sample_rate: int
    """The output audio sample rate."""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        voice: Literal["Puck", "Charon", "Kore", "Fenrir"] | str = "Puck",
        enable_input_audio_transcription: bool = True,
    ) -> None:
        """Initialize the GeminiRealtimeModel class.

        Args:
            model_name (`str`):
                The model name, e.g. "gemini-2.5-flash-native-audio-preview
                -09-2025". Refers to `official documentation
                <https://ai.google.dev/gemini-api/docs/live?hl=zh-cn&example=mic-stream>`_
                for more details.
            api_key (`str`):
                The Gemini API key for authentication.
            voice (`Literal["Puck", "Charon", "Kore", "Fenrir"] str`,
            defaults to `"Puck"`):
                The voice to be used for text-to-speech.
            enable_input_audio_transcription (`bool`, defaults to `True`):
                Whether to enable input audio transcription.
        """
        super().__init__(model_name)

        self.voice = voice
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )

        # The Gemini realtime API uses 16kHz input and 24kHz output.
        self.input_sample_rate = 16000
        self.output_sample_rate = 24000

        # Set the API key in the websocket URL.
        self.websocket_url = self.websocket_url + api_key

        # Response tracking state.
        # Note: Unlike DashScope/OpenAI which send explicit `response.created`
        # events, Gemini does not. We generate response IDs ourselves using
        # short UUID to ensure uniqueness.
        self._response_id: str | None = None

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build Gemini setup message.

        Gemini Live API requires a "setup" message as the first message
        to configure the session.

        Args:
            instructions (`str`):
                The system instructions for the model.
            tools (`list[dict]`):
                The list of tool JSON schemas.
            **kwargs:
                Additional configuration parameters.

        Returns:
            `dict`:
                The session configuration dict.
        """
        # Model configuration
        session_config: dict = {
            "model": f"models/{self.model_name}",
            "systemInstruction": {
                "parts": [{"text": instructions}],
            },
            "outputAudioTranscription": {},
        }

        # Audio transcription configuration
        if self.enable_input_audio_transcription:
            session_config["inputAudioTranscription"] = {}

        # Generation configuration
        generation_config: dict = {
            "responseModalities": ["AUDIO"],
            **kwargs,
        }

        # Voice configuration
        if self.voice:
            generation_config["speechConfig"] = {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": self.voice},
                },
            }

        session_config["generationConfig"] = generation_config

        # Tools configuration
        if tools:
            session_config["tools"] = self._format_toolkit_schema(tools)

        setup_msg = {"setup": session_config}
        return setup_msg

    def _format_toolkit_schema(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schema into Gemini format.

        Args:
            schemas (`list[dict[str, Any]]`):
                The tool schemas.

        Returns:
            `list[dict[str, Any]]`:
                The formatted tools for Gemini.
        """
        function_declarations = []
        for schema in schemas:
            if "function" not in schema:
                continue
            func = schema["function"].copy()
            function_declarations.append(func)

        return [{"function_declarations": function_declarations}]

    async def send(
        self,
        data: AudioBlock | TextBlock | ImageBlock | ToolResultBlock,
    ) -> None:
        """Send the data to the Gemini realtime model for processing.

        Args:
            data (`AudioBlock | TextBlock | ImageBlock | ToolResultBlock`):
                The data to be sent to the Gemini realtime model.
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
                "Gemini Realtime API does not support %s data input. "
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
                    name=data.get("name"),
                    output=data.get("output"),
                    id=data.get("id"),
                ),
            )

        else:
            raise RuntimeError(f"Unsupported data type {data_type}")

        if to_send_message:
            await self._websocket.send(to_send_message)

    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse the message received from the Gemini realtime model API.

        Args:
            message (`str`):
                The message received from the Gemini realtime model API.

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

        # ================ Setup related events ================
        if "setupComplete" in data:
            model_event = ModelEvents.ModelSessionCreatedEvent(
                session_id="gemini_session",
            )

        # ================ Server content events ================
        elif "serverContent" in data:
            model_event = await self._parse_server_content(
                data["serverContent"],
            )

        # ================ Tool call events ================
        elif "toolCall" in data:
            model_event = await self._parse_tool_call(data["toolCall"])

        # ================ Tool call cancellation ================
        elif "toolCallCancellation" in data:
            # Tool call was cancelled
            # This effectively ends the current response.
            logger.info(
                "Tool call cancelled: %s",
                data["toolCallCancellation"],
            )
            response_id = self._response_id or ""
            self._response_id = None  # Clear response ID
            model_event = ModelEvents.ModelResponseDoneEvent(
                response_id=response_id,
                input_tokens=0,
                output_tokens=0,
            )

        # ================ Error events ================
        elif "error" in data:
            error = data["error"]
            model_event = ModelEvents.ModelErrorEvent(
                error_type=error.get("status", "unknown"),
                code=str(error.get("code", "unknown")),
                message=error.get("message", "An unknown error occurred."),
            )

        else:
            logger.debug(
                "Unknown Gemini realtime model message keys: %s",
                list(data.keys()),
            )

        return model_event

    def _ensure_response_id(self) -> str:
        """Ensure a response ID exists, creating one if necessary.

        Gemini doesn't send explicit response.created events, so we generate
        the response ID on first audio/text chunk using short UUID.

        Returns:
            `str`: The current response ID.
        """
        if not self._response_id:
            self._response_id = f"resp_{shortuuid.uuid()}"
        # After the check above, _response_id is guaranteed to be non-None
        assert self._response_id is not None
        return self._response_id

    def _parse_model_turn(
        self,
        model_turn: dict,
    ) -> ModelEvents.EventBase | None:
        """Parse the modelTurn content from Gemini API.

        Args:
            model_turn (`dict`):
                The modelTurn dictionary containing parts with audio/text.

        Returns:
            `ModelEvents.EventBase | None`:
                The parsed model event, or None if no valid content found.
        """
        parts = model_turn.get("parts", [])

        for part in parts:
            # Check for audio data
            if "inlineData" in part:
                event = self._parse_inline_data(part["inlineData"])
                if event:
                    return event

            # Check for text data
            if "text" in part:
                text_data = part["text"]
                if text_data:
                    response_id = self._ensure_response_id()
                    return ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                        response_id=response_id,
                        delta=text_data,
                        item_id="",
                    )

        return None

    def _parse_inline_data(
        self,
        inline_data: dict,
    ) -> ModelEvents.EventBase | None:
        """Parse inline data (audio) from a model turn part.

        Args:
            inline_data (`dict`):
                The inlineData dictionary containing mimeType and data.

        Returns:
            `ModelEvents | None`:
                Audio delta event if valid audio data, None otherwise.
        """
        mime_type = inline_data.get("mimeType", "")
        if not mime_type.startswith("audio/"):
            return None

        audio_data = inline_data.get("data", "")
        if not audio_data:
            return None

        response_id = self._ensure_response_id()
        return ModelEvents.ModelResponseAudioDeltaEvent(
            response_id=response_id,
            item_id="",
            delta=audio_data,
            format={
                "type": "audio/pcm",
                "rate": self.output_sample_rate,
            },
        )

    async def _parse_server_content(
        self,
        server_content: dict,
    ) -> ModelEvents.EventBase | None:
        """Parse the serverContent message from Gemini API.

        Args:
            server_content (`dict`):
                The serverContent dictionary from the API response.

        Returns:
            `ModelEvents.EventBase | None`:
                The unified model event in agentscope format.
        """
        model_event = None

        # Handle model turn (response with audio/text)
        if "modelTurn" in server_content:
            model_event = self._parse_model_turn(server_content["modelTurn"])

        # Handle output transcription
        elif "outputTranscription" in server_content:
            text = server_content["outputTranscription"].get("text", "")
            if text:
                model_event = (
                    ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                        response_id=self._response_id or "",
                        delta=text,
                        item_id="",
                    )
                )

        # Handle input transcription
        elif "inputTranscription" in server_content:
            text = server_content["inputTranscription"].get("text", "")
            if text:
                model_event = ModelEvents.ModelInputTranscriptionDoneEvent(
                    transcript=text,
                    item_id="",
                )

        # Handle generation complete (response done)
        elif "generationComplete" in server_content:
            response_id = self._response_id or ""
            self._response_id = None
            model_event = ModelEvents.ModelResponseDoneEvent(
                response_id=response_id,
                input_tokens=0,
                output_tokens=0,
            )

        # Handle turn complete
        elif "turnComplete" in server_content:
            logger.debug("Gemini: turnComplete received")
            # turnComplete without generationComplete means interrupted
            if self._response_id:
                response_id = self._response_id
                self._response_id = None
                model_event = ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=0,
                    output_tokens=0,
                )

        # Handle interrupted
        elif "interrupted" in server_content:
            logger.debug("Gemini: response interrupted")

        return model_event

    async def _parse_tool_call(
        self,
        tool_call: dict,
    ) -> list[ModelEvents.EventBase] | None:
        """Parse the tool call message from Gemini API.

        Args:
            tool_call (`dict`):
                The toolCall dictionary from the API response.

        Returns:
            `list[ModelEvents.EventBase] | None`:
                The unified model event(s) in agentscope format.
        """
        function_calls = tool_call.get("functionCalls", [])
        if not function_calls:
            return None

        events = []
        for func_call in function_calls:
            name = func_call.get("name", "")
            call_id = func_call.get("id", "")
            args = func_call.get("args", {})

            # Return the accumulated arguments instead of just the delta
            model_event = ModelEvents.ModelResponseToolUseDoneEvent(
                response_id=self._response_id or "",
                item_id="",
                tool_use=ToolUseBlock(
                    type="tool_use",
                    id=call_id,
                    name=name,
                    input=args,
                    raw_input=json.dumps(args, ensure_ascii=False),
                ),
            )
            events.append(model_event)

        return events if events else None

    async def _parse_image_data(self, block: ImageBlock) -> str | None:
        """Parse the image data block to the format required by the Gemini
        realtime model API.

        Args:
            block (`ImageBlock`):
                The image data block.

        Returns:
            `str | None`: The parsed message to be sent to the Gemini realtime
            model API.
        """
        source = block.get("source", {})
        source_type = source.get("type", "")
        # media_type is in Base64Source, use default for URLSource
        media_type = source.get("media_type", "image/jpeg")

        if source_type == "base64":
            image_data = source.get("data", "")
        elif source_type == "url":
            image_data = _get_bytes_from_web_url(str(source.get("url", "")))
        else:
            raise ValueError(f"Unsupported image source type: {source_type}")

        return json.dumps(
            {
                "realtimeInput": {
                    "video": {
                        "mimeType": media_type,
                        "data": image_data,
                    },
                },
            },
        )

    async def _parse_audio_data(self, block: AudioBlock) -> str:
        """Parse the audio data block to the format required by the Gemini
        realtime model API.

        Args:
            block (`AudioBlock`):
                The audio data block.

        Returns:
            `str`: The parsed message to be sent to the Gemini realtime
            model API.
        """
        source = block.get("source", {})
        source_type = source.get("type", "")

        if source_type == "base64":
            audio_data = source.get("data", "")
        elif source_type == "url":
            audio_data = _get_bytes_from_web_url(str(source.get("url", "")))
        else:
            raise ValueError(f"Unsupported audio source type: {source_type}")

        return json.dumps(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": f"audio/pcm;rate={self.input_sample_rate}",
                        "data": audio_data,
                    },
                },
            },
        )

    async def _parse_text_data(self, block: TextBlock) -> str:
        """Parse the text data block to the format required by the Gemini
        realtime model API.

        Args:
            block (`TextBlock`):
                The text data block.

        Returns:
            `str`: The parsed message to be sent to the Gemini realtime
            model API.
        """
        text = block.get("text", "")

        return json.dumps(
            {
                "clientContent": {
                    "turns": [
                        {
                            "role": "user",
                            "parts": [{"text": text}],
                        },
                    ],
                    # TODO: should be set to False?
                    "turnComplete": True,
                },
            },
        )

    async def _parse_tool_result_data(self, block: ToolResultBlock) -> str:
        """Parse the tool result data block to the format required by the
        Gemini realtime model API.

        Args:
            block (`ToolResultBlock`):
                The tool result data block.

        Returns:
            `str`: The parsed message to be sent to the Gemini realtime
            model API.
        """
        tool_id = block.get("id", "")
        tool_name = block.get("name", "")
        output = block.get("output", "")

        # Extract text from list of blocks (most common case)
        if isinstance(output, list):
            text = "".join(
                str(item.get("text", ""))
                if isinstance(item, dict) and item.get("type") == "text"
                else str(item)
                for item in output
            )
            result_obj = {"result": text}
        elif isinstance(output, str):
            try:
                result_obj = json.loads(output)
            except json.JSONDecodeError:
                result_obj = {"result": output}
        else:
            result_obj = (
                output if isinstance(output, dict) else {"result": str(output)}
            )

        return json.dumps(
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": tool_id,
                            "name": tool_name,
                            "response": result_obj,
                        },
                    ],
                },
            },
        )
