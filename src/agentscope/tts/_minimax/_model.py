# -*- coding: utf-8 -*-
"""MiniMax TTS model implementation using the T2A HTTP API."""
import base64
import json
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_response import TTSResponse
from ...credential import MiniMaxCredential
from ...message import Base64Source, DataBlock

if TYPE_CHECKING:
    from httpx import AsyncClient


_TTS_PATH = "/v1/t2a_v2"
_MEDIA_TYPE = "audio/mpeg"


def _raise_for_api_error(payload: dict[str, Any]) -> None:
    """Raise when the API reports an unsuccessful status code."""
    base_response = payload.get("base_resp") or {}
    status_code = base_response.get("status_code", 0)
    if status_code != 0:
        status_message = base_response.get("status_msg", "unknown error")
        raise RuntimeError(
            f"MiniMax TTS API error {status_code}: {status_message}",
        )


class MiniMaxTTSModel(TTSModelBase):
    """MiniMax TTS model implementation using the T2A HTTP API.

    For more details, see the `official documentation
    <https://platform.minimax.io/docs/api-reference/speech-t2a-http>`_.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for MiniMax TTS models."""

        voice: str = Field(
            default="English_expressive_narrator",
            title="Voice",
            description="The voice ID to use for synthesis.",
        )

        speed: float = Field(
            default=1.0,
            title="Speed",
            description="The speech speed multiplier.",
        )

        volume: float = Field(
            default=1.0,
            title="Volume",
            description="The output volume multiplier.",
        )

        pitch: int = Field(
            default=0,
            title="Pitch",
            description="The voice pitch adjustment.",
        )

        sample_rate: int = Field(
            default=32000,
            title="Sample Rate",
            description="The audio sample rate in hertz.",
        )

        bitrate: int = Field(
            default=128000,
            title="Bitrate",
            description="The audio bitrate in bits per second.",
        )

        channel: int = Field(
            default=1,
            title="Channels",
            description="The number of audio channels.",
        )

        language_boost: str | None = Field(
            default="auto",
            title="Language Boost",
            description="The language or dialect optimization mode.",
        )

    type: Literal["minimax_tts"] = "minimax_tts"
    """The type of the TTS model."""

    realtime: bool = False

    def __init__(
        self,
        credential: MiniMaxCredential,
        model: str = "speech-2.8-hd",
        parameters: "MiniMaxTTSModel.Parameters | None" = None,
        stream: bool = True,
    ) -> None:
        """Initialize the MiniMax TTS model.

        Args:
            credential (`MiniMaxCredential`):
                The credential used to authenticate the API call.
            model (`str`, defaults to ``"speech-2.8-hd"``):
                The TTS model name.
            parameters (`MiniMaxTTSModel.Parameters | None`, defaults to \
            `None`):
                The voice, audio, and language parameters.
            stream (`bool`, defaults to `True`):
                Whether to stream incremental audio responses.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )

        import httpx

        self.client: AsyncClient = httpx.AsyncClient(
            headers={
                "Authorization": (
                    "Bearer " + self.credential.api_key.get_secret_value()
                ),
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Synthesize speech through the T2A HTTP endpoint.

        Args:
            text (`str | None`, optional):
                The text to synthesize.
            **kwargs (`Any`):
                Additional fields to include in the API request.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single response or a stream of incremental audio chunks.
        """
        if not text:
            return TTSResponse(content=None)

        payload: dict[str, Any] = {
            "voice_setting": {
                "voice_id": self.parameters.voice,
                "speed": self.parameters.speed,
                "vol": self.parameters.volume,
                "pitch": self.parameters.pitch,
            },
            "audio_setting": {
                "sample_rate": self.parameters.sample_rate,
                "bitrate": self.parameters.bitrate,
                "format": "mp3",
                "channel": self.parameters.channel,
            },
            **kwargs,
            "model": self.model,
            "text": text,
            "stream": self.stream,
            "output_format": "hex",
        }
        if self.parameters.language_boost is not None:
            payload.setdefault(
                "language_boost",
                self.parameters.language_boost,
            )

        audio_setting = payload.get("audio_setting")
        if isinstance(audio_setting, dict):
            payload["audio_setting"] = {
                **audio_setting,
                "format": "mp3",
            }
        else:
            payload["audio_setting"] = {"format": "mp3"}

        if self.stream:
            stream_options = payload.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            payload["stream_options"] = {
                **stream_options,
                "exclude_aggregated_audio": True,
            }
            return self._stream(payload)

        return await self._aggregate(payload)

    async def _aggregate(
        self,
        payload: dict[str, Any],
    ) -> TTSResponse:
        """Return the complete audio from a non-streaming response."""
        response = await self.client.post(
            self.credential.base_url.rstrip("/") + _TTS_PATH,
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        _raise_for_api_error(result)

        data = result.get("data") or {}
        audio = data.get("audio")
        if not audio:
            return TTSResponse(content=None)

        return TTSResponse(
            content=DataBlock(
                source=Base64Source(
                    data=base64.b64encode(bytes.fromhex(audio)).decode(
                        "ascii",
                    ),
                    media_type=_MEDIA_TYPE,
                ),
            ),
        )

    async def _stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Yield incremental audio chunks from a streaming response."""
        pending: bytes | None = None
        emitted = bytearray()

        async with self.client.stream(
            "POST",
            self.credential.base_url.rstrip("/") + _TTS_PATH,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    line = line.removeprefix("data:").strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    continue

                _raise_for_api_error(result)
                data = result.get("data") or {}
                audio = data.get("audio")
                if not audio:
                    continue

                audio_bytes = bytes.fromhex(audio)
                if data.get("status") != 2:
                    if pending is not None:
                        emitted.extend(pending)
                        yield self._audio_response(pending, is_last=False)
                    pending = audio_bytes
                    continue

                received = bytes(emitted) + (pending or b"")
                if audio_bytes.startswith(received):
                    final_audio = audio_bytes[len(received) :]
                else:
                    final_audio = audio_bytes

                if pending is not None:
                    yield self._audio_response(
                        pending,
                        is_last=not final_audio,
                    )
                if final_audio:
                    yield self._audio_response(final_audio, is_last=True)
                elif pending is None:
                    yield TTSResponse(content=None, is_last=True)
                return

        if pending is not None:
            yield self._audio_response(pending, is_last=True)
        else:
            yield TTSResponse(content=None, is_last=True)

    @staticmethod
    def _audio_response(audio: bytes, is_last: bool) -> TTSResponse:
        """Build a response for an MP3 audio chunk."""
        return TTSResponse(
            content=DataBlock(
                source=Base64Source(
                    data=base64.b64encode(audio).decode("ascii"),
                    media_type=_MEDIA_TYPE,
                ),
            ),
            is_last=is_last,
        )
