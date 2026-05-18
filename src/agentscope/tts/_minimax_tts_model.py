# -*- coding: utf-8 -*-
"""MiniMax TTS model implementation."""
import base64
import json
from typing import Any, AsyncGenerator

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject

_DEFAULT_MINIMAX_TTS_URL = "https://api.minimax.io/v1/t2a_v2"


class MiniMaxTTSModel(TTSModelBase):
    """MiniMax TTS model implementation using the T2A v2 HTTP API.
    For more details, please see the `official document
    <https://platform.minimax.io/docs/api-reference/speech-t2a-http>`_.
    """

    supports_streaming_input: bool = False

    def __init__(
        self,
        api_key: str,
        model_name: str = "speech-2.8-hd",
        voice_id: str = "English_Graceful_Lady",
        stream: bool = True,
        api_url: str | None = None,
        voice_setting: dict[str, Any] | None = None,
        audio_setting: dict[str, Any] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, stream=stream)

        self.api_key = api_key
        self.voice_id = voice_id
        self.api_url = api_url or _DEFAULT_MINIMAX_TTS_URL
        self.voice_setting = voice_setting or {}
        self.audio_setting = audio_setting or {}
        self.generate_kwargs = generate_kwargs or {}

        import httpx

        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def synthesize(
        self,
        msg: Msg | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        if msg is None:
            return TTSResponse(content=None)

        text = msg.get_text_content()

        if not text:
            return TTSResponse(content=None)

        voice_setting = {"voice_id": self.voice_id, **self.voice_setting}
        audio_setting = self.audio_setting.copy()
        if "format" not in audio_setting:
            audio_setting["format"] = "mp3"

        payload: dict[str, Any] = {
            "model": self.model_name,
            "text": text,
            "stream": self.stream,
            "voice_setting": voice_setting,
            "audio_setting": audio_setting,
            **self.generate_kwargs,
            **kwargs,
        }

        media_type = f"audio/{audio_setting['format']}"

        if self.stream:
            return self._stream_synthesize(payload, media_type)

        response = await self._client.post(
            self.api_url,
            json=payload,
        )
        response.raise_for_status()
        result = response.json()

        if result.get("base_resp", {}).get("status_code", 0) != 0:
            raise RuntimeError(
                f"MiniMax TTS API error: "
                f"{result.get('base_resp', {}).get('status_msg', 'unknown')}",
            )

        hex_audio = result.get("data", {}).get("audio", "")
        if not hex_audio:
            return TTSResponse(content=None)

        audio_bytes = bytes.fromhex(hex_audio)
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return TTSResponse(
            content=AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=audio_base64,
                    media_type=media_type,
                ),
            ),
        )

    async def _stream_synthesize(
        self,
        payload: dict[str, Any],
        media_type: str,
    ) -> AsyncGenerator[TTSResponse, None]:
        async with self._client.stream(
            "POST",
            self.api_url,
            json=payload,
        ) as response:
            response.raise_for_status()
            buffer = ""
            async for chunk_text in response.aiter_text():
                buffer += chunk_text
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        chunk_data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    base_resp = chunk_data.get("base_resp", {})
                    if base_resp.get("status_code", 0) != 0:
                        raise RuntimeError(
                            f"MiniMax TTS API error: "
                            f"{base_resp.get('status_msg', 'unknown')}",
                        )

                    hex_audio = (
                        chunk_data.get("data", {}).get("audio", "")
                    )
                    if not hex_audio:
                        continue

                    audio_bytes = bytes.fromhex(hex_audio)
                    audio_base64 = base64.b64encode(audio_bytes).decode(
                        "utf-8",
                    )

                    status = chunk_data.get("data", {}).get("status", 1)
                    is_last = status == 2

                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=audio_base64,
                                media_type=media_type,
                            ),
                        ),
                        is_last=is_last,
                    )
